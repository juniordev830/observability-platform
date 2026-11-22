import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen
from server import new_server


class QueryBoundTests(unittest.TestCase):
    def test_rejects_excessively_repeated_query_fields(self):
        server = new_server(port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            with self.assertRaises(HTTPError) as error:
                urlopen(base + "/events?after=1&after=2&limit=1&category=x")
            self.assertEqual(error.exception.code, 400)
            with urlopen(base + "/events?after=0&limit=2") as response:
                self.assertEqual(response.status, 200)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
