"""Internal-only HTTP endpoint for the n8n scheduler."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from .core import ingest

_lock = threading.Lock()

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != '/run':
            self.send_error(404)
            return
        if not _lock.acquire(blocking=False):
            self.reply(409, {'error': 'An ingestion run is already active'})
            return
        try:
            result = ingest('/app/config/datasets.json', '/archive', os.environ.get('DATAGOLF_API_KEY', ''))
            self.reply(200 if result['success'] else 502, result)
        except Exception as exc:
            self.reply(500, {'error': str(exc)})
        finally:
            _lock.release()

    def reply(self, status, result):
        body = json.dumps(result).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # Suppress request logging to avoid accidental URL or header disclosure.
        pass

if __name__ == '__main__':
    ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
