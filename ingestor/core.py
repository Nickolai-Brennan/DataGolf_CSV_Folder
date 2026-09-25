"""Fetch documented Data Golf CSV feeds and archive validated snapshots."""
import csv
import hashlib
import io
import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

HOST = 'https://feeds.datagolf.com'
NAME = re.compile(r'^[a-z][a-z0-9_]*$')


def ingest(config_path, output_root, api_key, opener=urlopen, now=None, sleep=time.sleep,
           routed_root=None, schema_path=None):
    if not api_key:
        raise ValueError('DATAGOLF_API_KEY is required')
    root = Path(output_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    configured = json.loads(Path(config_path).read_text(encoding='utf-8'))
    datasets = configured.get('datasets')
    if not isinstance(datasets, list):
        raise ValueError('Configuration must contain a datasets list')
    run_id = str(uuid.uuid4())
    results = []
    for entry in datasets:
        if not entry.get('enabled', False):
            continue
        name = entry.get('name', '')
        path = entry.get('path', '')
        if not NAME.fullmatch(name) or not re.fullmatch(r'/[a-z0-9/-]+', path):
            raise ValueError('Invalid dataset name or endpoint path')
        params = entry.get('params', {})
        if not isinstance(params, dict) or any(k in params for k in ('key', 'file_format')):
            raise ValueError('Dataset params must exclude key and file_format')
        started = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        record = {'run_id': run_id, 'dataset': name, 'started_at': started.isoformat(), 'status': 'failed'}
        try:
            url = HOST + path + '?' + urlencode({**params, 'file_format': 'csv', 'key': api_key})
            payload = None
            for attempt in range(3):
                try:
                    with opener(Request(url, headers={'Accept': 'text/csv', 'User-Agent': 'datagolf-ingestion/1.0'}), timeout=40) as response:
                        payload = response.read()
                        content_type = response.headers.get('Content-Type', '')
                    break
                except HTTPError as exc:
                    if exc.code not in (429, 500, 502, 503, 504) or attempt == 2:
                        raise RuntimeError(f'Data Golf HTTP {exc.code}') from None
                    sleep(2 ** attempt)
                except (URLError, TimeoutError) as exc:
                    if attempt == 2:
                        raise RuntimeError('Data Golf request failed after retries') from exc
                    sleep(2 ** attempt)
            if 'json' in content_type.lower() or 'html' in content_type.lower():
                raise ValueError('Expected CSV response')
            decoded = payload.decode('utf-8-sig')
            rows = list(csv.reader(io.StringIO(decoded, newline='')))
            if len(rows) < 2 or not rows[0] or any(not h.strip() for h in rows[0]):
                raise ValueError('CSV has no header or data rows')
            if len(set(rows[0])) != len(rows[0]) or any(len(row) != len(rows[0]) for row in rows[1:]):
                raise ValueError('CSV columns are inconsistent')
            digest = hashlib.sha256(payload).hexdigest()
            directory = root / name / started.strftime('%Y/%m/%d')
            directory.mkdir(parents=True, exist_ok=True)
            filename = f"{name}_{started.strftime('%Y%m%dT%H%M%S')}_{run_id[:8]}.csv"
            destination = directory / filename
            temporary = destination.with_suffix('.csv.tmp')
            with temporary.open('xb') as handle:
                handle.write(payload)
            os.replace(temporary, destination)
            # Keep the raw copy even if routing fails so a later scan can retry.
            if routed_root is not None:
                from .router import process_file
                routed = process_file(destination, entry, root, routed_root, schema_path)
                record['routed_file'] = routed['output_path']
            record.update(status='success', row_count=len(rows) - 1, sha256=digest,
                          file_path=str(destination), bytes=len(payload))
        except Exception as exc:
            # Never include the authenticated URL or response body in logs.
            record['error'] = str(exc)
        record['completed_at'] = datetime.now(timezone.utc).isoformat()
        results.append(record)
        with (root / 'ingestion_runs.jsonl').open('a', encoding='utf-8') as log:
            log.write(json.dumps(record) + '\n')
        sleep(2)  # At most 30 requests per minute on sustained successful runs.
    return {'run_id': run_id, 'results': results, 'success': all(r['status'] == 'success' for r in results)}
