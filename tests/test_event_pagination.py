import unittest

from telemetry import EventLog


class EventPaginationTests(unittest.TestCase):
    def test_pages_do_not_skip_events(self):
        events = EventLog(capacity=10)
        for index in range(7):
            events.append('job', str(index))
        seen, cursor = [], 0
        while page := events.list(after=cursor, limit=2):
            seen.extend(item['sequence'] for item in page)
            cursor = page[-1]['sequence']
        self.assertEqual(seen, list(range(1, 8)))

    def test_filtered_pages_do_not_skip_events(self):
        events = EventLog(capacity=10)
        for index in range(8):
            events.append('job' if index % 2 == 0 else 'deploy', str(index))
        first = events.list(category='job', limit=2)
        second = events.list(category='job', after=first[-1]['sequence'], limit=2)
        self.assertEqual([item['sequence'] for item in first + second], [1, 3, 5, 7])

    def test_append_result_cannot_mutate_history(self):
        events = EventLog()
        event = events.append('job', 'Started', {'state': 'original'})
        event.attributes['state'] = 'changed'
        self.assertEqual(events.list()[0]['attributes'], {'state': 'original'})

    def test_boolean_cursor_is_rejected(self):
        with self.assertRaises(ValueError):
            EventLog().list(after=True)

    def test_pagination_starts_at_oldest_retained_event(self):
        events = EventLog(capacity=3)
        for index in range(5):
            events.append('job', str(index))
        self.assertEqual([item['sequence'] for item in events.list(limit=2)], [3, 4])
