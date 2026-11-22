"""Reject Boolean values for numeric alert/incident options."""
import unittest
from monitoring import AlertRule, IncidentStore, SLOTracker


class NumericConfigurationTests(unittest.TestCase):
    def test_bool_cannot_be_used_for_alert_hold_time(self):
        for value in (True, False):
            with self.subTest(value=value), self.assertRaises(ValueError):
                AlertRule("cpu", 90, for_seconds=value)

    def test_bool_cannot_be_used_as_incident_capacity(self):
        for value in (True, False):
            with self.subTest(value=value), self.assertRaises(ValueError):
                IncidentStore(max_incidents=value)

    def test_legitimate_numeric_configurations_still_work(self):
        self.assertEqual(AlertRule("cpu", 90, for_seconds=1).for_seconds, 1)
        self.assertEqual(IncidentStore(max_incidents=1).max_incidents, 1)
        self.assertEqual(SLOTracker(capacity=1).capacity, 1)


if __name__ == "__main__":
    unittest.main()
