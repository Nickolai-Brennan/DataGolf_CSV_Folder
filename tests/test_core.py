"""Offline checks for CSV archiving and error logging."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from ingestor.core import ingest

class FakeResponse:
    headers = {'Content-Type': 'text/csv'}
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self): return b'player_id,name\n1,Alice\n2,Bob\n'

class IngestionTests(unittest.TestCase):
    def test_snapshot_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'config.json'
            config.write_text(json.dumps({'datasets': [{'name': 'players', 'path': '/get-player-list', 'enabled': True}]}))
            result = ingest(config, Path(tmp) / 'archive', 'test-key', opener=lambda *a, **k: FakeResponse(),
                            now=datetime(2026, 9, 25, tzinfo=timezone.utc), sleep=lambda _: None)
            self.assertTrue(result['success'])
            record = result['results'][0]
            self.assertEqual(record['row_count'], 2)
            self.assertTrue(Path(record['file_path']).exists())
            self.assertNotIn('test-key', (Path(tmp) / 'archive/ingestion_runs.jsonl').read_text())

    def test_invalid_csv_is_not_saved(self):
        class BadResponse(FakeResponse):
            def read(self): return b'{"error":"unauthorized"}'
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'config.json'
            config.write_text(json.dumps({'datasets': [{'name': 'players', 'path': '/get-player-list', 'enabled': True}]}))
            result = ingest(config, Path(tmp) / 'archive', 'test-key', opener=lambda *a, **k: BadResponse(), sleep=lambda _: None)
            self.assertFalse(result['success'])
            self.assertEqual(list((Path(tmp) / 'archive').rglob('*.csv')), [])

if __name__ == '__main__':
    unittest.main()
