import threading
import unittest
from urllib.request import urlopen
from server import new_server

class MetricsServerTests(unittest.TestCase):
    def test_health_and_prometheus_metrics(self):
        srv = new_server(port=0)
        thread = threading.Thread(target=srv.serve_forever, daemon=True)
        thread.start()
        try:
            url = f'http://127.0.0.1:{srv.server_port}'
            with urlopen(url+'/health') as health:
                self.assertIn(b'"ok"', health.read())
                self.assertEqual(health.headers['Cache-Control'], 'no-store')
                self.assertEqual(health.headers['X-Content-Type-Options'], 'nosniff')
            text = urlopen(url+'/metrics').read().decode()
            self.assertIn('demo_requests_total{path="/health"} 1', text)
            self.assertIn('demo_uptime_seconds', text)
        finally:
            srv.shutdown(); srv.server_close(); thread.join(timeout=3)
if __name__ == '__main__': unittest.main()
