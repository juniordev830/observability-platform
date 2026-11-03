"""Local-only Prometheus-compatible demo metrics endpoint; no dependencies."""
from __future__ import annotations
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from time import monotonic

class Metrics:
    def __init__(self):
        self.started = monotonic()
        self.calls = {'/health': 0, '/ready': 0, '/metrics': 0, '/other': 0}
        self.lock = Lock()
    def observe(self, path: str) -> None:
        key = path if path in {'/health','/ready','/metrics'} else '/other'
        with self.lock:
            self.calls[key] += 1
    def render(self) -> str:
        with self.lock:
            calls = dict(self.calls)
        lines = ['# HELP demo_requests_total HTTP requests served',
                 '# TYPE demo_requests_total counter']
        for path, count in sorted(calls.items()):
            lines.append(f'demo_requests_total{{path="{path}"}} {count}')
        lines += ['# HELP demo_uptime_seconds Time since process started',
                  '# TYPE demo_uptime_seconds gauge',f'demo_uptime_seconds {monotonic()-self.started:.3f}']
        return '\n'.join(lines) + '\n'

def new_server(host='127.0.0.1', port=9091):
    metrics = Metrics()
    class Handler(BaseHTTPRequestHandler):
        def respond(self, include_body):
            path = self.path.split('?',1)[0]
            metrics.observe(path)
            if path == '/health':
                body = b'{"status":"ok"}'
                content_type = 'application/json'
                status = 200
            elif path == '/ready':
                body = b'{"status":"ready"}'
                content_type = 'application/json'
                status = 200
            elif path == '/metrics':
                body = metrics.render().encode()
                content_type = 'text/plain; version=0.0.4; charset=utf-8'
                status = 200
            else:
                body = b'Not found'
                content_type = 'text/plain'
                status = 404
            self.send_response(status)
            self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            if include_body:
                self.wfile.write(body)
        def do_GET(self):
            self.respond(include_body=True)
        def do_HEAD(self):
            self.respond(include_body=False)
        def log_message(self, format, *args):
            pass  # Avoid logging potentially sensitive paths by default.
    return ThreadingHTTPServer((host,port),Handler)

if __name__ == '__main__':
    server = new_server()
    print('Metrics at http://127.0.0.1:9091/metrics')
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
