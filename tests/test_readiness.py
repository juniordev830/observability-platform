import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from server import new_server


class ReadinessTests(unittest.TestCase):
    def test_ready_and_head_are_counted_without_head_body(self):
        server = new_server(port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f'http://127.0.0.1:{server.server_port}'
            with urlopen(base + '/ready') as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), b'{"status":"ready"}')
            with urlopen(Request(base + '/ready', method='HEAD')) as response:
                self.assertEqual(response.status, 200)
                self.assertEqual(response.headers['Content-Length'], str(len(b'{"status":"ready"}')))
                self.assertEqual(response.read(), b'')
            with urlopen(base + '/metrics') as response:
                self.assertIn('demo_requests_total{path="/ready"} 2', response.read().decode())
            with self.assertRaises(HTTPError) as failure:
                urlopen(Request(base + '/unknown', method='HEAD'))
            self.assertEqual(failure.exception.code, 404)
            self.assertEqual(failure.exception.read(), b'')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == '__main__':
    unittest.main()
