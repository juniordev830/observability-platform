"""Local-only HTTP observability server with Prometheus and JSON diagnostics.

No third-party dependencies. Do not expose to untrusted networks: endpoints
intentionally have no authentication and the stores are process-local.
"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from time import monotonic
from urllib.parse import parse_qs, urlsplit

from monitoring import AlertManager, HealthRegistry, IncidentStore, SLOTracker
from telemetry import EventLog, MetricRegistry


class Metrics:
    """Preserves the original demo counters and uptime exposition format."""

    def __init__(self):
        self.started = monotonic()
        self.calls = {"/health": 0, "/ready": 0, "/metrics": 0, "/other": 0}
        self.lock = Lock()

    def observe(self, path: str) -> None:
        key = path if path in {"/health", "/ready", "/metrics"} else "/other"
        with self.lock:
            self.calls[key] += 1

    def render(self) -> str:
        with self.lock:
            calls = dict(self.calls)
        lines = ["# HELP demo_requests_total HTTP requests served",
                 "# TYPE demo_requests_total counter"]
        for path, count in sorted(calls.items()):
            lines.append(f'demo_requests_total{{path="{path}"}} {count}')
        lines += ["# HELP demo_uptime_seconds Time since process started",
                  "# TYPE demo_uptime_seconds gauge",
                  f"demo_uptime_seconds {monotonic()-self.started:.3f}"]
        return "\n".join(lines) + "\n"


def new_server(host="127.0.0.1", port=9091, health_checks: HealthRegistry | None = None):
    """Create a server. The caller owns serve_forever(), shutdown() and close()."""
    metrics = Metrics()
    registry = MetricRegistry(max_series=64)
    registry.register_counter("http_responses_total", "HTTP responses by status",
                              labels=("method", "status"))
    registry.register_histogram("http_request_duration_seconds", "HTTP request duration",
                                labels=("method",), buckets=(0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 5))
    events = EventLog(capacity=500)
    alerts = AlertManager()
    incidents = IncidentStore()
    slo = SLOTracker(target=0.99, window_seconds=3600)
    health = health_checks if health_checks is not None else HealthRegistry()
    observability = {"metrics": registry, "events": events, "alerts": alerts,
                     "incidents": incidents, "slo": slo, "health": health}

    class Handler(BaseHTTPRequestHandler):
        def _json(self, data) -> bytes:
            return json.dumps(data, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":")).encode("utf-8")

        def _route(self, path: str, query: str) -> tuple[int, str, bytes]:
            if path == "/health":
                return 200, "application/json", b'{"status":"ok"}'
            if path == "/ready":
                ready = health.evaluate().healthy
                return (200 if ready else 503, "application/json",
                        b'{"status":"ready"}' if ready else b'{"status":"not_ready"}')
            if path == "/metrics":
                return 200, "text/plain; version=0.0.4; charset=utf-8", (
                    metrics.render() + registry.render()).encode("utf-8")
            if path == "/health/details":
                report = health.evaluate()
                return (200 if report.healthy else 503, "application/json",
                        self._json(report.to_dict()))
            if path == "/summary":
                return 200, "application/json", self._json({
                    "uptime_seconds": max(0.0, monotonic() - metrics.started),
                    "slo": slo.snapshot().to_dict(),
                    "incidents": incidents.summary(),
                    "alerts": alerts.active(),
                    "events_retained": events.count(),
                    "metrics": registry.snapshot(),
                })
            if path == "/alerts":
                return 200, "application/json", self._json(alerts.status())
            if path == "/incidents":
                return 200, "application/json", self._json(incidents.list())
            if path == "/events":
                parameters = parse_qs(query, keep_blank_values=True)
                allowed = {"category", "after", "limit"}
                if set(parameters) - allowed or any(len(values) != 1 for values in parameters.values()):
                    return 400, "application/json", self._json({"error": "invalid query"})
                try:
                    after = int(parameters.get("after", ["0"])[0])
                    limit = int(parameters.get("limit", ["100"])[0])
                    category = parameters.get("category", [None])[0]
                    items = events.list(category=category, after=after, limit=limit)
                except (ValueError, TypeError):
                    return 400, "application/json", self._json({"error": "invalid pagination"})
                return 200, "application/json", self._json(items)
            return 404, "text/plain", b"Not found"

        def respond(self, include_body: bool) -> None:
            started = monotonic()
            path_parts = urlsplit(self.path)
            path = path_parts.path
            metrics.observe(path)
            status, content_type, body = self._route(path, path_parts.query)
            method = "GET" if include_body else "HEAD"
            registry.increment("http_responses_total", labels={
                "method": method, "status": str(status)})
            registry.observe("http_request_duration_seconds",
                             max(0.0, monotonic() - started), labels={"method": method})
            slo.record(status < 500)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if include_body:
                self.wfile.write(body)

        def do_GET(self):
            self.respond(include_body=True)

        def do_HEAD(self):
            self.respond(include_body=False)

        def log_message(self, format, *args):
            pass  # Avoid logging potentially sensitive paths by default.

    server = ThreadingHTTPServer((host, port), Handler)
    server.observability = observability
    return server


if __name__ == "__main__":
    server = new_server()
    print("Metrics at http://127.0.0.1:9091/metrics")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
