"""Unit and concurrency tests for metric series, windows, and event retention."""
import json
import threading
import unittest

from telemetry import (
    EventLog, MetricDefinition, MetricError, MetricRegistry, RollingWindow,
    escape_label, validate_name,
)


class FakeClock:
    def __init__(self, start=0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class MetricDefinitionTests(unittest.TestCase):
    def test_accepts_prometheus_metric_names(self):
        for name in ("requests_total", "_private", ":internal", "a2_b3"):
            with self.subTest(name=name):
                self.assertEqual(validate_name(name), name)

    def test_rejects_invalid_metric_names(self):
        for name in ("", "2starts", "has-dash", "has space", "a.b", None):
            with self.subTest(name=name):
                with self.assertRaises(MetricError):
                    validate_name(name)

    def test_label_names_cannot_be_reserved_or_colon_names(self):
        for label in ("__name__", "foo:bar", "1foo", ""):
            with self.subTest(label=label):
                with self.assertRaises(MetricError):
                    validate_name(label, label=True)

    def test_escapes_all_special_characters(self):
        self.assertEqual(escape_label('a"b\\c\nz'), 'a\\"b\\\\c\\nz')

    def test_requires_unique_labels(self):
        with self.assertRaises(MetricError):
            MetricDefinition("requests", "counter", "help", ("method", "method"))

    def test_requires_nonempty_help(self):
        with self.assertRaises(MetricError):
            MetricDefinition("requests", "counter", " ")

    def test_rejects_unknown_metric_type(self):
        with self.assertRaises(MetricError):
            MetricDefinition("requests", "summary", "help")

    def test_histogram_requires_buckets(self):
        with self.assertRaises(MetricError):
            MetricDefinition("duration", "histogram", "help")

    def test_histogram_rejects_unsorted_buckets(self):
        with self.assertRaises(MetricError):
            MetricDefinition("duration", "histogram", "help", (), (2, 1))

    def test_histogram_rejects_duplicate_buckets(self):
        with self.assertRaises(MetricError):
            MetricDefinition("duration", "histogram", "help", (), (1, 1))

    def test_histogram_rejects_nonfinite_buckets(self):
        for bad in (float("inf"), float("nan")):
            with self.subTest(bad=bad):
                with self.assertRaises(MetricError):
                    MetricDefinition("duration", "histogram", "help", (), (1, bad))

    def test_histogram_reserves_le_label(self):
        with self.assertRaises(MetricError):
            MetricDefinition("duration", "histogram", "help", ("le",), (1, 2))

    def test_nonhistogram_rejects_buckets(self):
        with self.assertRaises(MetricError):
            MetricDefinition("requests", "counter", "help", (), (1, 2))


class MetricRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = MetricRegistry(max_series=4)
        self.registry.register_counter("requests_total", "Total requests", ("route",))
        self.registry.register_gauge("queue_depth", "Queue depth")
        self.registry.register_histogram("latency_seconds", "Latency", ("route",), (0.1, 0.5, 1))

    def test_empty_registry_renders_metadata(self):
        rendered = self.registry.render()
        self.assertIn("# TYPE requests_total counter", rendered)
        self.assertIn("# TYPE queue_depth gauge", rendered)
        self.assertNotIn("requests_total{", rendered)

    def test_counter_increments_and_renders(self):
        self.registry.increment("requests_total", labels={"route": "/ok"})
        self.registry.increment("requests_total", 2, labels={"route": "/ok"})
        self.assertIn('requests_total{route="/ok"} 3', self.registry.render())

    def test_counter_cannot_decrease(self):
        with self.assertRaises(MetricError):
            self.registry.increment("requests_total", -1, {"route": "/"})

    def test_counter_rejects_nonfinite_values(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(MetricError):
                    self.registry.increment("requests_total", bad, {"route": "/"})

    def test_boolean_is_not_numeric(self):
        with self.assertRaises(MetricError):
            self.registry.increment("requests_total", True, {"route": "/"})

    def test_gauge_can_be_set_and_adjusted_below_zero(self):
        self.registry.set_gauge("queue_depth", 3)
        self.registry.adjust_gauge("queue_depth", -5)
        self.assertIn("queue_depth -2", self.registry.render())

    def test_counter_rejects_gauge_operations(self):
        with self.assertRaises(MetricError):
            self.registry.set_gauge("requests_total", 1, {"route": "/"})

    def test_gauge_rejects_counter_operations(self):
        with self.assertRaises(MetricError):
            self.registry.increment("queue_depth", 1)

    def test_histogram_cumulative_buckets(self):
        for value in (0.05, 0.5, 2):
            self.registry.observe("latency_seconds", value, {"route": "/a"})
        output = self.registry.render()
        self.assertIn('latency_seconds_bucket{le="0.1",route="/a"} 1', output)
        self.assertIn('latency_seconds_bucket{le="0.5",route="/a"} 2', output)
        self.assertIn('latency_seconds_bucket{le="1",route="/a"} 2', output)
        self.assertIn('latency_seconds_bucket{le="+Inf",route="/a"} 3', output)
        self.assertIn('latency_seconds_count{route="/a"} 3', output)
        self.assertIn('latency_seconds_sum{route="/a"} 2.55', output)

    def test_histogram_requires_observe(self):
        with self.assertRaises(MetricError):
            self.registry.increment("latency_seconds", labels={"route": "/"})

    def test_histogram_accepts_negative_observations(self):
        self.registry.observe("latency_seconds", -1, {"route": "/"})
        self.assertEqual(self.registry.snapshot()["latency_seconds"]["series"][0]["count"], 1)

    def test_missing_labels_rejected(self):
        with self.assertRaises(MetricError):
            self.registry.increment("requests_total")

    def test_extra_labels_rejected(self):
        with self.assertRaises(MetricError):
            self.registry.increment("requests_total", labels={"route": "/", "extra": "x"})

    def test_nonstring_labels_rejected(self):
        with self.assertRaises(MetricError):
            self.registry.increment("requests_total", labels={"route": 123})

    def test_long_labels_rejected(self):
        with self.assertRaises(MetricError):
            self.registry.increment("requests_total", labels={"route": "a" * 201})

    def test_unknown_metric_rejected(self):
        with self.assertRaises(MetricError):
            self.registry.increment("unknown")

    def test_duplicate_registration_rejected(self):
        with self.assertRaises(MetricError):
            self.registry.register_counter("requests_total", "Again")

    def test_histogram_generated_name_collision(self):
        with self.assertRaises(MetricError):
            self.registry.register_counter("latency_seconds_count", "Collision")

    def test_limit_blocks_new_series_without_changing_old(self):
        for route in ("/1", "/2", "/3", "/4"):
            self.registry.increment("requests_total", labels={"route": route})
        with self.assertRaises(MetricError):
            self.registry.increment("requests_total", labels={"route": "/5"})
        self.assertEqual(len(self.registry.snapshot()["requests_total"]["series"]), 4)

    def test_reset_one_metric_preserves_other_series(self):
        self.registry.increment("requests_total", labels={"route": "/"})
        self.registry.set_gauge("queue_depth", 10)
        self.registry.reset("requests_total")
        self.assertEqual(self.registry.snapshot()["requests_total"]["series"], [])
        self.assertEqual(self.registry.snapshot()["queue_depth"]["series"][0]["value"], 10)

    def test_reset_all_clears_series_but_keeps_definitions(self):
        self.registry.set_gauge("queue_depth", 1)
        self.registry.reset()
        self.assertEqual(self.registry.snapshot()["queue_depth"]["series"], [])
        self.registry.set_gauge("queue_depth", 2)
        self.assertIn("queue_depth 2", self.registry.render())

    def test_snapshot_is_defensive_copy(self):
        self.registry.increment("requests_total", labels={"route": "/"})
        snapshot = self.registry.snapshot()
        snapshot["requests_total"]["series"][0]["labels"]["route"] = "corrupted"
        self.assertIn('route="/"', self.registry.render())

    def test_prometheus_label_escaping(self):
        self.registry.increment("requests_total", labels={"route": 'a"b\\c\n'})
        self.assertIn('route="a\\"b\\\\c\\n"', self.registry.render())

    def test_timer_records_even_if_body_raises(self):
        clock = FakeClock()
        with self.assertRaises(RuntimeError):
            with self.registry.time("latency_seconds", {"route": "/err"}, clock):
                clock.advance(0.25)
                raise RuntimeError("expected")
        series = self.registry.snapshot()["latency_seconds"]["series"]
        self.assertEqual(series[0]["sum"], 0.25)

    def test_concurrent_counter_updates_are_atomic(self):
        registry = MetricRegistry()
        registry.register_counter("jobs_total", "Jobs")
        workers = [threading.Thread(target=lambda: [registry.increment("jobs_total") for _ in range(500)])
                   for _ in range(8)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join()
        self.assertEqual(registry.snapshot()["jobs_total"]["series"][0]["value"], 4000)

    def test_invalid_series_limit_rejected(self):
        for limit in (0, -1, True, 1.2):
            with self.subTest(limit=limit):
                with self.assertRaises(MetricError):
                    MetricRegistry(limit)


class RollingWindowTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock(100)
        self.window = RollingWindow(10, capacity=3, clock=self.clock)

    def test_empty_window_has_null_percentiles(self):
        self.assertEqual(self.window.count(), 0)
        self.assertIsNone(self.window.average())
        self.assertIsNone(self.window.percentile(50))
        self.assertEqual(self.window.rate(), 0)

    def test_add_and_calculate_summary(self):
        for value in (1, 2, 3):
            self.window.add(value)
        self.assertEqual(self.window.count(), 3)
        self.assertEqual(self.window.total(), 6)
        self.assertEqual(self.window.average(), 2)
        self.assertEqual(self.window.percentile(50), 2)
        self.assertEqual(self.window.percentile(95), 2.9)
        self.assertAlmostEqual(self.window.rate(), 0.3)

    def test_expiry_removes_old_samples(self):
        self.window.add(10)
        self.clock.advance(11)
        self.window.add(2)
        self.assertEqual(self.window.values(), [2])

    def test_exact_window_boundary_is_included(self):
        self.window.add(1)
        self.clock.advance(10)
        self.assertEqual(self.window.count(), 1)
        self.clock.advance(0.001)
        self.assertEqual(self.window.count(), 0)

    def test_capacity_evicts_oldest(self):
        for value in (1, 2, 3, 4):
            self.window.add(value)
        self.assertEqual(self.window.values(), [2, 3, 4])

    def test_percentile_interpolates(self):
        self.window.add(10)
        self.window.add(20)
        self.assertEqual(self.window.percentile(25), 12.5)

    def test_invalid_percentile_rejected(self):
        for percent in (-1, 101, float("nan")):
            with self.subTest(percent=percent):
                with self.assertRaises(ValueError):
                    self.window.percentile(percent)

    def test_nonfinite_observations_rejected(self):
        with self.assertRaises(MetricError):
            self.window.add(float("inf"))

    def test_window_validation(self):
        for seconds in (0, -1, float("inf")):
            with self.subTest(seconds=seconds):
                with self.assertRaises(ValueError):
                    RollingWindow(seconds)


class EventLogTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock(200)
        self.events = EventLog(capacity=3, clock=self.clock)

    def test_append_and_list(self):
        event = self.events.append("deploy", "Service started", {"region": "local"})
        self.assertEqual(event.sequence, 1)
        self.assertEqual(event.timestamp, 200)
        self.assertEqual(self.events.list()[0]["attributes"]["region"], "local")

    def test_capacity_retains_newest(self):
        for number in range(5):
            self.events.append("job", f"Job {number}")
        self.assertEqual([item["sequence"] for item in self.events.list()], [3, 4, 5])

    def test_cursor_returns_only_later_events(self):
        for number in range(3):
            self.events.append("job", f"Job {number}")
        self.assertEqual([item["sequence"] for item in self.events.list(after=1)], [2, 3])

    def test_filter_category(self):
        self.events.append("deploy", "First")
        self.events.append("incident", "Second")
        self.assertEqual(len(self.events.list(category="deploy")), 1)

    def test_limit_applies_after_filter(self):
        self.events.append("job", "One")
        self.events.append("job", "Two")
        self.events.append("job", "Three")
        self.assertEqual([item["message"] for item in self.events.list(limit=2)], ["One", "Two"])

    def test_attributes_are_copied(self):
        attributes = {"environment": "test"}
        self.events.append("deploy", "Ready", attributes)
        attributes["environment"] = "prod"
        self.assertEqual(self.events.list()[0]["attributes"]["environment"], "test")

    def test_list_results_are_defensive(self):
        self.events.append("deploy", "Ready", {"key": "value"})
        items = self.events.list()
        items[0]["attributes"]["key"] = "changed"
        self.assertEqual(self.events.list()[0]["attributes"]["key"], "value")

    def test_rejects_invalid_categories(self):
        for category in ("", "UPPER", "has space", "a" * 65):
            with self.subTest(category=category):
                with self.assertRaises(ValueError):
                    self.events.append(category, "Message")

    def test_rejects_empty_or_oversized_message(self):
        for message in ("", "   ", "x" * 501):
            with self.subTest(message=message[:10]):
                with self.assertRaises(ValueError):
                    self.events.append("job", message)

    def test_rejects_nonstring_attributes(self):
        with self.assertRaises(ValueError):
            self.events.append("job", "Ready", {"attempts": 2})

    def test_rejects_too_many_attributes(self):
        with self.assertRaises(ValueError):
            self.events.append("job", "Ready", {str(i): "x" for i in range(17)})

    def test_clear_does_not_reuse_sequences(self):
        self.events.append("job", "One")
        self.events.clear()
        self.assertEqual(self.events.count(), 0)
        self.assertEqual(self.events.append("job", "Two").sequence, 2)

    def test_invalid_cursor_and_limit(self):
        for args in ({"after": -1}, {"limit": 0}, {"limit": 4}):
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    self.events.list(**args)

    def test_event_is_json_serializable(self):
        self.events.append("job", "Started", {"id": "1"})
        self.assertEqual(json.loads(json.dumps(self.events.list()))[0]["category"], "job")


if __name__ == "__main__":
    unittest.main()

