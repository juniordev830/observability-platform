"""HTTP integration coverage: compatibility, JSON diagnostics and isolation."""
import json
import threading
import unittest
from contextlib import contextmanager
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from monitoring import AlertRule, HealthRegistry
from server import new_server


@contextmanager
def running_server(**kwargs):
    server = new_server(port=0, **kwargs)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def read_json(url, method="GET"):
    with urlopen(Request(url, method=method), timeout=3) as response:
        return response, json.loads(response.read())


class DiagnosticsHttpTests(unittest.TestCase):
    def test_summary_contains_slo_and_telemetry(self):
        with running_server() as (server, base):
            response, data = read_json(base + "/summary")
            self.assertEqual(response.status, 200)
            self.assertIn("uptime_seconds", data)
            self.assertIn("slo", data)
            self.assertIn("metrics", data)
            self.assertIn("incidents", data)
            self.assertIn("events_retained", data)
            self.assertIn("http_responses_total", data["metrics"])

    def test_events_endpoint_supports_cursor_category_and_limit(self):
        with running_server() as (server, base):
            events = server.observability["events"]
            events.append("deploy", "First")
            events.append("incident", "Second")
            events.append("deploy", "Third")
            _, data = read_json(base + "/events?category=deploy&after=1&limit=2")
            self.assertEqual([item["message"] for item in data], ["Third"])

    def test_invalid_event_query_returns_400(self):
        with running_server() as (_, base):
            for query in ("limit=0", "after=-1", "limit=notanumber", "unknown=1",
                          "after=1&after=2", "limit=999999"):
                with self.subTest(query=query):
                    with self.assertRaises(HTTPError) as error:
                        urlopen(base + "/events?" + query, timeout=3)
                    self.assertEqual(error.exception.code, 400)

    def test_alerts_endpoint_reflects_registered_rules(self):
        with running_server() as (server, base):
            alerts = server.observability["alerts"]
            alerts.register(AlertRule("high_error_rate", 0.1))
            alerts.evaluate("high_error_rate", 0.5)
            _, data = read_json(base + "/alerts")
            self.assertEqual(data["high_error_rate"]["state"], "firing")

    def test_incidents_endpoint_returns_created_incidents(self):
        with running_server() as (server, base):
            store = server.observability["incidents"]
            store.create("Demo outage", "critical")
            _, data = read_json(base + "/incidents")
            self.assertEqual(data[0]["title"], "Demo outage")

    def test_detailed_health_with_optional_failure_stays_healthy(self):
        health = HealthRegistry()
        health.register("cache", lambda: False, required=False)
        with running_server(health_checks=health) as (_, base):
            response, data = read_json(base + "/health/details")
            self.assertEqual(response.status, 200)
            self.assertEqual(data["status"], "healthy")
            self.assertFalse(data["checks"][0]["healthy"])

    def test_failed_required_dependency_changes_readiness(self):
        health = HealthRegistry()
        health.register("database", lambda: (False, "down"))
        with running_server(health_checks=health) as (_, base):
            with self.assertRaises(HTTPError) as error:
                urlopen(base + "/ready", timeout=3)
            self.assertEqual(error.exception.code, 503)
            self.assertEqual(error.exception.read(), b'{"status":"not_ready"}')
            with self.assertRaises(HTTPError) as error:
                urlopen(base + "/health/details", timeout=3)
            self.assertEqual(error.exception.code, 503)

    def test_head_has_no_body_but_correct_content_length(self):
        with running_server() as (_, base):
            with urlopen(Request(base + "/alerts", method="HEAD"), timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertGreater(int(response.headers["Content-Length"]), 0)
                self.assertEqual(response.read(), b"")

    def test_prometheus_has_original_and_new_metrics(self):
        with running_server() as (_, base):
            urlopen(base + "/health", timeout=3).read()
            urlopen(base + "/summary", timeout=3).read()
            with urlopen(base + "/metrics", timeout=3) as response:
                body = response.read().decode()
            self.assertIn('demo_requests_total{path="/health"} 1', body)
            self.assertIn("demo_uptime_seconds", body)
            self.assertIn("# TYPE http_responses_total counter", body)
            self.assertIn('http_responses_total{method="GET",status="200"}', body)
            self.assertIn("http_request_duration_seconds_bucket", body)

    def test_slo_tracks_failed_readiness(self):
        health = HealthRegistry()
        health.register("database", lambda: False)
        with running_server(health_checks=health) as (_, base):
            try:
                urlopen(base + "/ready", timeout=3)
            except HTTPError:
                pass
            _, data = read_json(base + "/summary")
            self.assertEqual(data["slo"]["errors"], 1)
            self.assertEqual(data["slo"]["total"], 1)

    def test_server_instances_do_not_share_state(self):
        with running_server() as (first, first_url):
            first.observability["events"].append("deploy", "First")
            with running_server() as (second, second_url):
                _, events = read_json(second_url + "/events")
                self.assertEqual(events, [])
                _, events = read_json(first_url + "/events")
                self.assertEqual(len(events), 1)

    def test_no_store_security_headers(self):
        with running_server() as (_, base):
            with urlopen(base + "/summary", timeout=3) as response:
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

    def test_unknown_endpoint_remains_404(self):
        with running_server() as (_, base):
            with self.assertRaises(HTTPError) as error:
                urlopen(base + "/does-not-exist", timeout=3)
            self.assertEqual(error.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
