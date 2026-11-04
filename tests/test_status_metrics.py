import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from server import Metrics, new_server


class StatusMetricsTests(unittest.TestCase):
    def test_status_counters_include_health_not_found_and_head(self):
        server = new_server(port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f'http://127.0.0.1:{server.server_port}'
            with urlopen(base + '/health') as response:
                self.assertEqual(response.status, 200)
            with urlopen(Request(base + '/ready', method='HEAD')) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), b'')
            with self.assertRaises(HTTPError) as error:
                urlopen(base + '/missing')
            self.assertEqual(error.exception.code, 404)

            with urlopen(base + '/metrics') as response:
                report = response.read().decode()
            self.assertIn('demo_responses_total{status="200"} 3', report)
            self.assertIn('demo_responses_total{status="404"} 1', report)
            self.assertIn('demo_requests_total{path="/other"} 1', report)
            self.assertIn('# TYPE demo_responses_total counter', report)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_concurrent_observations_are_not_lost(self):
        metrics = Metrics()
        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(lambda _: metrics.observe('/unknown', 404), range(1000)))
        report = metrics.render()
        self.assertIn('demo_responses_total{status="404"} 1000', report)
        self.assertIn('demo_requests_total{path="/other"} 1000', report)
        with self.assertRaises(ValueError):
            metrics.observe('/health', 500)
        self.assertIn('demo_responses_total{status="200"} 0', metrics.render())


if __name__ == '__main__':
    unittest.main()
