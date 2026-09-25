"""Internal-only HTTP endpoint for the n8n scheduler."""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from .core import ingest
from .router import process_pending

_lock = threading.Lock()
CONFIG = '/app/config/datasets.json'
ARCHIVE = '/archive'
ROUTED = '/classified'
SCHEMA = '/app/config/schema.sql'


def watch_archive():
    """Pick up completed manual uploads and retry raw files after mapping fixes."""
    while True:
        try:
            with _lock:
                datasets = json.loads(open(CONFIG, encoding='utf-8').read())['datasets']
                for result in process_pending(datasets, ARCHIVE, ROUTED, SCHEMA):
                    if result['status'] == 'failed':
                        print(f"Routing failed for {result['raw_path']}: {result['error']}", flush=True)
        except Exception as exc:
            print(f'Archive scan failed: {exc}', flush=True)
        time.sleep(15)

class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != '/run':
            self.send_error(404)
            return
        if not _lock.acquire(blocking=False):
            self.reply(409, {'error': 'An ingestion run is already active'})
            return
        try:
            result = ingest(CONFIG, ARCHIVE, os.environ.get('DATAGOLF_API_KEY', ''),
                            routed_root=ROUTED, schema_path=SCHEMA)
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
    threading.Thread(target=watch_archive, daemon=True).start()
    ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
