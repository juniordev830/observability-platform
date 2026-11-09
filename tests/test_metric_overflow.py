import json
import unittest

from telemetry import MetricError, MetricRegistry


class MetricOverflowTests(unittest.TestCase):
    def test_counter_overflow_preserves_previous_value(self):
        registry = MetricRegistry()
        registry.register_counter('count_total', 'Count')
        registry.increment('count_total', 1e308)
        before = registry.snapshot()
        with self.assertRaises(MetricError):
            registry.increment('count_total', 1e308)
        self.assertEqual(registry.snapshot(), before)
        json.dumps(registry.snapshot(), allow_nan=False)

    def test_gauge_overflow_preserves_previous_value(self):
        for sign in (-1, 1):
            with self.subTest(sign=sign):
                registry = MetricRegistry()
                registry.register_gauge('load', 'Load')
                registry.set_gauge('load', sign * 1e308)
                before = registry.snapshot()
                with self.assertRaises(MetricError):
                    registry.adjust_gauge('load', sign * 1e308)
                self.assertEqual(registry.snapshot(), before)

    def test_histogram_overflow_does_not_change_count_or_buckets(self):
        registry = MetricRegistry()
        registry.register_histogram('size', 'Size', buckets=(0.0, 1.0, 1e308))
        registry.observe('size', 1e308)
        before = registry.snapshot()
        with self.assertRaises(MetricError):
            registry.observe('size', 1e308)
        self.assertEqual(registry.snapshot(), before)
        registry.observe('size', -1e308)
        series = registry.snapshot()['size']['series'][0]
        self.assertEqual(series['count'], 2)
        self.assertEqual(series['sum'], 0)

    def test_oversized_integer_raises_domain_error_without_allocating_series(self):
        registry = MetricRegistry()
        registry.register_counter('count_total', 'Count')
        with self.assertRaises(MetricError):
            registry.increment('count_total', 10 ** 1000)
        self.assertEqual(registry.snapshot()['count_total']['series'], [])
