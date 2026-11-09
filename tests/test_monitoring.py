"""Behavioral tests for readiness, SLO budgets, alerts and incident state."""
import json
import threading
import unittest

from monitoring import (
    AlertManager, AlertRule, HealthRegistry, IncidentStore, SLOTracker,
)


class Clock:
    def __init__(self, value=100.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class HealthRegistryTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.health = HealthRegistry(clock=self.clock)

    def test_empty_registry_is_healthy(self):
        self.assertTrue(self.health.evaluate().healthy)
        self.assertEqual(self.health.evaluate().checks, ())

    def test_successful_required_check(self):
        self.health.register("database", lambda: True)
        report = self.health.evaluate()
        self.assertTrue(report.healthy)
        self.assertEqual(report.checks[0].name, "database")
        self.assertEqual(report.checks[0].detail, "ok")

    def test_failed_required_check_marks_unhealthy(self):
        self.health.register("database", lambda: False)
        self.assertFalse(self.health.evaluate().healthy)

    def test_optional_failure_does_not_mark_unhealthy(self):
        self.health.register("cache", lambda: False, required=False)
        report = self.health.evaluate()
        self.assertTrue(report.healthy)
        self.assertFalse(report.checks[0].healthy)

    def test_check_can_return_reason(self):
        self.health.register("db", lambda: (False, "connection refused"))
        self.assertEqual(self.health.evaluate().checks[0].detail, "connection refused")

    def test_exception_does_not_escape(self):
        def broken():
            raise ConnectionError("sensitive connection string")
        self.health.register("db", broken)
        report = self.health.evaluate()
        self.assertFalse(report.healthy)
        self.assertEqual(report.checks[0].detail, "ConnectionError")
        self.assertNotIn("sensitive", str(report.to_dict()))

    def test_invalid_check_response_is_failure(self):
        self.health.register("db", lambda: "ok")
        self.assertEqual(self.health.evaluate().checks[0].detail, "invalid check response")

    def test_check_names_sorted(self):
        self.health.register("zeta", lambda: True)
        self.health.register("alpha", lambda: True)
        self.assertEqual(self.health.names(), ["alpha", "zeta"])
        self.assertEqual([check.name for check in self.health.evaluate().checks], ["alpha", "zeta"])

    def test_duplicate_registration_rejected(self):
        self.health.register("db", lambda: True)
        with self.assertRaises(ValueError):
            self.health.register("db", lambda: True)

    def test_unregister(self):
        self.health.register("db", lambda: False)
        self.assertTrue(self.health.unregister("db"))
        self.assertFalse(self.health.unregister("db"))
        self.assertTrue(self.health.evaluate().healthy)

    def test_invalid_name_and_callback_rejected(self):
        with self.assertRaises(ValueError):
            self.health.register("", lambda: True)
        with self.assertRaises(TypeError):
            self.health.register("db", None)

    def test_duration_is_nonnegative(self):
        def check():
            self.clock.advance(0.25)
            return True
        self.health.register("slow", check)
        self.assertAlmostEqual(self.health.evaluate().checks[0].duration_ms, 250)

    def test_report_is_json_serializable(self):
        self.health.register("db", lambda: True)
        self.assertEqual(json.loads(json.dumps(self.health.evaluate().to_dict()))["status"], "healthy")

    def test_health_summary_distinguishes_optional_and_required_failures(self):
        self.health.register("database", lambda: False)
        self.health.register("cache", lambda: False, required=False)
        self.health.register("search", lambda: True)
        report = self.health.evaluate()
        self.assertFalse(report.healthy)
        self.assertEqual(report.to_dict()["summary"], {
            "total": 3, "required": 2, "optional": 1,
            "failed_required": 1, "failed_optional": 1,
        })

    def test_empty_health_summary_has_zero_counts(self):
        self.assertEqual(self.health.evaluate().to_dict()["summary"], {
            "total": 0, "required": 0, "optional": 0,
            "failed_required": 0, "failed_optional": 0,
        })


class SLOTrackerTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.slo = SLOTracker(target=0.9, window_seconds=60, clock=self.clock)

    def test_no_traffic_is_unknown_compliance(self):
        snapshot = self.slo.snapshot()
        self.assertEqual(snapshot.total, 0)
        self.assertIsNone(snapshot.observed_ratio)
        self.assertIsNone(snapshot.compliant)

    def test_all_successful_is_compliant(self):
        for _ in range(10):
            self.slo.record(True)
        snapshot = self.slo.snapshot()
        self.assertTrue(snapshot.compliant)
        self.assertEqual(snapshot.observed_ratio, 1)
        self.assertEqual(snapshot.burn_rate, 0)

    def test_errors_consume_budget(self):
        for _ in range(9):
            self.slo.record(True)
        self.slo.record(False)
        snapshot = self.slo.snapshot()
        self.assertEqual(snapshot.errors, 1)
        self.assertAlmostEqual(snapshot.error_budget, 1)
        self.assertAlmostEqual(snapshot.burn_rate, 1)
        self.assertTrue(snapshot.compliant)

    def test_excess_errors_break_target(self):
        for _ in range(8):
            self.slo.record(True)
        for _ in range(2):
            self.slo.record(False)
        self.assertFalse(self.slo.snapshot().compliant)
        self.assertLess(self.slo.snapshot().budget_remaining, 0)

    def test_old_samples_expire(self):
        self.slo.record(False)
        self.clock.advance(61)
        self.slo.record(True)
        self.assertEqual(self.slo.snapshot().total, 1)
        self.assertEqual(self.slo.snapshot().errors, 0)

    def test_boundary_sample_included(self):
        self.slo.record(False)
        self.clock.advance(60)
        self.assertEqual(self.slo.snapshot().total, 1)
        self.clock.advance(0.01)
        self.assertEqual(self.slo.snapshot().total, 0)

    def test_capacity_limits_history(self):
        tracker = SLOTracker(target=0.9, capacity=2, clock=self.clock)
        tracker.record(False)
        tracker.record(True)
        tracker.record(True)
        self.assertEqual(tracker.snapshot().total, 2)
        self.assertEqual(tracker.snapshot().errors, 0)

    def test_reset(self):
        self.slo.record(False)
        self.slo.reset()
        self.assertEqual(self.slo.snapshot().total, 0)

    def test_record_requires_boolean(self):
        with self.assertRaises(TypeError):
            self.slo.record(1)

    def test_invalid_targets(self):
        for target in (0, 1, -0.1, float("nan"), True):
            with self.subTest(target=target):
                with self.assertRaises(ValueError):
                    SLOTracker(target=target)

    def test_invalid_windows(self):
        for window in (0, -1, float("inf")):
            with self.subTest(window=window):
                with self.assertRaises(ValueError):
                    SLOTracker(window_seconds=window)

    def test_snapshot_can_be_serialized(self):
        self.slo.record(True)
        data = json.loads(json.dumps(self.slo.snapshot().to_dict()))
        self.assertEqual(data["successful"], 1)


class AlertRuleTests(unittest.TestCase):
    def test_all_comparators(self):
        cases = [
            (">", 11, True), (">", 10, False),
            (">=", 10, True), ("<", 9, True),
            ("<", 10, False), ("<=", 10, True),
            ("==", 10, True), ("!=", 11, True),
        ]
        for operator, value, expected in cases:
            with self.subTest(operator=operator, value=value):
                self.assertEqual(AlertRule("cpu", 10, operator).matches(value), expected)

    def test_invalid_operator_rejected(self):
        with self.assertRaises(ValueError):
            AlertRule("cpu", 10, "~")

    def test_invalid_severity_rejected(self):
        with self.assertRaises(ValueError):
            AlertRule("cpu", 10, severity="emergency")

    def test_invalid_threshold_rejected(self):
        for threshold in (float("nan"), float("inf"), True):
            with self.subTest(threshold=threshold):
                with self.assertRaises(ValueError):
                    AlertRule("cpu", threshold)

    def test_invalid_for_seconds_rejected(self):
        with self.assertRaises(ValueError):
            AlertRule("cpu", 10, for_seconds=-1)

    def test_nonfinite_observation_rejected(self):
        with self.assertRaises(ValueError):
            AlertRule("cpu", 10).matches(float("nan"))


class AlertManagerTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.alerts = AlertManager(clock=self.clock)
        self.alerts.register(AlertRule("high_latency", 0.5, ">", for_seconds=10,
                                       severity="critical"))

    def test_breach_enters_pending(self):
        self.assertEqual(self.alerts.evaluate("high_latency", 0.8), "pending")
        self.assertEqual(self.alerts.active(), {})

    def test_sustained_breach_fires(self):
        self.alerts.evaluate("high_latency", 0.8)
        self.clock.advance(10)
        self.assertEqual(self.alerts.evaluate("high_latency", 0.9), "firing")
        self.assertIn("high_latency", self.alerts.active())

    def test_recovery_clears_alert(self):
        self.alerts.evaluate("high_latency", 0.8)
        self.clock.advance(11)
        self.alerts.evaluate("high_latency", 0.8)
        self.assertEqual(self.alerts.evaluate("high_latency", 0.1), "inactive")
        self.assertEqual(self.alerts.active(), {})

    def test_pending_resets_after_recovery(self):
        self.alerts.evaluate("high_latency", 0.8)
        self.clock.advance(8)
        self.alerts.evaluate("high_latency", 0.1)
        self.clock.advance(8)
        self.assertEqual(self.alerts.evaluate("high_latency", 0.8), "pending")

    def test_instant_rule_fires_immediately(self):
        self.alerts.register(AlertRule("errors", 0))
        self.assertEqual(self.alerts.evaluate("errors", 1), "firing")

    def test_silence_hides_firing_but_preserves_state(self):
        self.alerts.evaluate("high_latency", 0.8)
        self.clock.advance(11)
        self.alerts.evaluate("high_latency", 0.8)
        self.alerts.silence("high_latency", 30)
        self.assertEqual(self.alerts.active(), {})
        self.assertIn("high_latency", self.alerts.active(include_silenced=True))
        self.clock.advance(31)
        self.assertIn("high_latency", self.alerts.active())

    def test_invalid_silence_duration(self):
        for duration in (0, -1, float("inf"), True):
            with self.subTest(duration=duration):
                with self.assertRaises(ValueError):
                    self.alerts.silence("high_latency", duration)

    def test_duplicate_rule_rejected(self):
        with self.assertRaises(ValueError):
            self.alerts.register(AlertRule("high_latency", 0.5))

    def test_remove_rule(self):
        self.assertTrue(self.alerts.remove("high_latency"))
        self.assertFalse(self.alerts.remove("high_latency"))
        self.assertEqual(self.alerts.status(), {})

    def test_transition_history_records_only_changes(self):
        self.alerts.evaluate("high_latency", 0.8)
        self.alerts.evaluate("high_latency", 0.9)
        self.clock.advance(10)
        self.alerts.evaluate("high_latency", 0.9)
        self.alerts.evaluate("high_latency", 0.1)
        history = self.alerts.history()
        self.assertEqual([event["current"] for event in history],
                         ["pending", "firing", "inactive"])
        self.assertEqual([event["sequence"] for event in history], [1, 2, 3])

    def test_history_cursor(self):
        self.alerts.evaluate("high_latency", 0.8)
        self.clock.advance(10)
        self.alerts.evaluate("high_latency", 0.8)
        self.assertEqual(len(self.alerts.history(after=1)), 1)

    def test_evaluate_all(self):
        self.alerts.register(AlertRule("errors", 0))
        result = self.alerts.evaluate_all({"errors": 2, "high_latency": 0.1})
        self.assertEqual(result["errors"], "firing")
        self.assertEqual(result["high_latency"], "inactive")

    def test_evaluate_all_rejects_unknown_rules(self):
        with self.assertRaises(KeyError):
            self.alerts.evaluate_all({"unknown": 5})

    def test_status_is_defensive_copy(self):
        self.alerts.evaluate("high_latency", 0.8)
        status = self.alerts.status()
        status["high_latency"]["state"] = "corrupted"
        self.assertEqual(self.alerts.status()["high_latency"]["state"], "pending")

    def test_concurrent_evaluation_does_not_lose_history(self):
        self.alerts.register(AlertRule("errors", 0))
        threads = [threading.Thread(target=lambda: self.alerts.evaluate("errors", 1))
                   for _ in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(self.alerts.status()["errors"]["state"], "firing")
        self.assertEqual(len([x for x in self.alerts.history() if x["name"] == "errors"]), 1)


class IncidentStoreTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(1000)
        self.incidents = IncidentStore(clock=self.clock, max_incidents=3)

    def test_create_with_unique_ids(self):
        first = self.incidents.create("DB outage", "critical")
        second = self.incidents.create("Slow response")
        self.assertEqual((first["id"], second["id"]), (1, 2))
        self.assertEqual(self.incidents.summary()["open"], 2)

    def test_acknowledge_then_resolve(self):
        incident = self.incidents.create("DB outage")
        self.clock.advance(5)
        acknowledged = self.incidents.acknowledge(incident["id"])
        self.assertEqual(acknowledged["state"], "acknowledged")
        self.assertEqual(acknowledged["acknowledged_at"], 1005)
        self.clock.advance(5)
        resolved = self.incidents.resolve(incident["id"], "Service restored")
        self.assertEqual(resolved["state"], "resolved")
        self.assertEqual(resolved["resolved_at"], 1010)
        self.assertEqual(resolved["notes"], ["Service restored"])

    def test_acknowledge_is_idempotent(self):
        incident = self.incidents.create("DB outage")
        self.incidents.acknowledge(incident["id"])
        self.clock.advance(10)
        self.assertEqual(self.incidents.acknowledge(incident["id"])["acknowledged_at"], 1000)

    def test_cannot_acknowledge_resolved(self):
        incident = self.incidents.create("DB outage")
        self.incidents.resolve(incident["id"])
        with self.assertRaises(ValueError):
            self.incidents.acknowledge(incident["id"])

    def test_cannot_resolve_twice(self):
        incident = self.incidents.create("DB outage")
        self.incidents.resolve(incident["id"])
        with self.assertRaises(ValueError):
            self.incidents.resolve(incident["id"])

    def test_notes_are_defensive_copies(self):
        incident = self.incidents.create("DB outage")
        self.incidents.add_note(incident["id"], "Investigating")
        result = self.incidents.get(incident["id"])
        result["notes"].append("Tampered")
        self.assertEqual(self.incidents.get(incident["id"])["notes"], ["Investigating"])

    def test_rejects_invalid_title(self):
        for title in ("", " ", "x" * 201):
            with self.subTest(title=title[:10]):
                with self.assertRaises(ValueError):
                    self.incidents.create(title)

    def test_rejects_invalid_severity(self):
        with self.assertRaises(ValueError):
            self.incidents.create("Outage", "urgent")

    def test_rejects_invalid_notes(self):
        incident = self.incidents.create("Outage")
        for note in ("", " ", "x" * 501):
            with self.subTest(note=note[:10]):
                with self.assertRaises(ValueError):
                    self.incidents.add_note(incident["id"], note)

    def test_capacity_rejects_extra_incidents(self):
        for index in range(3):
            self.incidents.create(f"Outage {index}")
        with self.assertRaises(RuntimeError):
            self.incidents.create("Outage 4")

    def test_filter_by_state_and_severity(self):
        first = self.incidents.create("DB outage", "critical")
        self.incidents.create("Slow", "warning")
        self.incidents.resolve(first["id"])
        self.assertEqual(len(self.incidents.list(state="resolved")), 1)
        self.assertEqual(len(self.incidents.list(severity="critical")), 1)
        self.assertEqual(len(self.incidents.list(state="open", severity="critical")), 0)

    def test_summary_counts_each_state(self):
        first = self.incidents.create("One")
        second = self.incidents.create("Two")
        self.incidents.create("Three")
        self.incidents.acknowledge(first["id"])
        self.incidents.resolve(second["id"])
        self.assertEqual(self.incidents.summary(),
                         {"total": 3, "open": 1, "acknowledged": 1, "resolved": 1})

    def test_unknown_id_raises_key_error(self):
        with self.assertRaises(KeyError):
            self.incidents.get(999)

    def test_invalid_filter_rejected(self):
        with self.assertRaises(ValueError):
            self.incidents.list(state="invalid")

    def test_incident_is_json_serializable(self):
        self.incidents.create("Outage")
        self.assertEqual(json.loads(json.dumps(self.incidents.list()))[0]["title"], "Outage")


if __name__ == "__main__":
    unittest.main()
