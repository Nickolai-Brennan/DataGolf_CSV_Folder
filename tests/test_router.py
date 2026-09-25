"""File routing and mapping identity checks using isolated CSV fixtures."""
import os
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ingestor.core import ingest
from ingestor.lookup import lookup
from ingestor.router import process_file, process_pending

SCHEMA = Path(__file__).resolve().parents[1] / 'config' / 'schema.sql'

PLAYER = {
    'name': 'player_list', 'entity': 'players', 'enabled': True,
    'mappings': [{'entity': 'players', 'name_columns': ['player_name'],
                  'datagolf_id_columns': ['dg_id'], 'pgatour_id_columns': ['pga_tour_id']}]
}
EVENT = {
    'name': 'pga_schedule', 'entity': 'events', 'enabled': True,
    'mappings': [
        {'entity': 'events', 'tour': 'pga', 'name_columns': ['event_name'], 'datagolf_id_columns': ['event_id']},
        {'entity': 'courses', 'required': False, 'name_columns': ['course_name'], 'datagolf_id_columns': ['course_id']}
    ]
}
COURSE = {
    'name': 'course_catalog', 'entity': 'courses', 'source_type': 'manual', 'enabled': True,
    'mappings': [{'entity': 'courses', 'name_columns': ['course_name'],
                  'datagolf_id_columns': ['course_id']}]
}
EVENT_STATS_INDEX = {
    **EVENT, 'name': 'historical_event_stats_index', 'event_index_kind': 'event_stats',
    'params': {'tour': 'pga'}
}
ROUNDS_INDEX = {
    **EVENT, 'name': 'historical_rounds_index', 'event_index_kind': 'rounds',
    'params': {'tour': 'pga'}
}


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.raw = self.root / 'data'
        self.raw.mkdir()
        self.routed = self.root / 'CSV Files'

    def write_raw(self, dataset, filename, content):
        folder = self.raw / 'inbox' / dataset
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / filename
        path.write_text(content, encoding='utf-8')
        return path

    def test_player_file_is_copied_and_upserted_once(self):
        source = self.write_raw('player_list', 'upload.csv', 'dg_id,player_name,pga_tour_id\n001,Alex,42\n')
        result = process_file(source, PLAYER, self.raw, self.routed, SCHEMA)
        self.assertEqual(result['status'], 'processed')
        self.assertEqual(Path(result['output_path']).read_bytes(), source.read_bytes())
        self.assertEqual(process_file(source, PLAYER, self.raw, self.routed, SCHEMA)['status'], 'already_processed')
        with sqlite3.connect(self.raw / 'mappings.sqlite3') as db:
            self.assertEqual(db.execute('SELECT id,name,datagolf_id,pgatour_id FROM player_mapping').fetchall(),
                             [(1, 'Alex', '001', '42')])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM processed_files').fetchone()[0], 1)

    def test_schedule_updates_event_and_course_tables(self):
        source = self.write_raw('pga_schedule', 'schedule.csv',
                                'event_id,event_name,course_id,course_name\n12,Open,5,Old Course\n')
        result = process_file(source, EVENT, self.raw, self.routed, SCHEMA)
        self.assertIn('/events/', result['output_path'])
        with sqlite3.connect(self.raw / 'mappings.sqlite3') as db:
            self.assertEqual(db.execute('SELECT name,datagolf_id,tour FROM event_mapping').fetchall(),
                             [('Open', '12', 'pga')])
            self.assertEqual(db.execute('SELECT name,datagolf_id FROM course_mapping').fetchall(),
                             [('Old Course', '5')])

    def test_manual_course_catalog_routes_to_courses_folder(self):
        source = self.write_raw('course_catalog', 'courses.csv',
                                'course_id,course_name\n05,Old Course\n')
        result = process_file(source, COURSE, self.raw, self.routed, SCHEMA)
        self.assertIn('/courses/', result['output_path'])
        with sqlite3.connect(self.raw / 'mappings.sqlite3') as db:
            self.assertEqual(db.execute('SELECT name,datagolf_id FROM course_mapping').fetchone(),
                             ('Old Course', '05'))

    def test_index_tracks_event_years_and_source_coverage(self):
        stats = self.write_raw('historical_event_stats_index', 'stats.csv',
                               'tour,calendar_year,date,event_name,event_id\n'
                               'pga,2026,2026-08-30,TOUR Championship,60\n')
        rounds = self.write_raw('historical_rounds_index', 'rounds.csv',
                                'tour,calendar_year,date,event_name,event_id,sg_categories,traditional_stats\n'
                                'pga,2026,2026-08-30,TOUR Championship,60,yes,yes\n'
                                'pga,2025,2025-08-24,TOUR Championship,60,yes,yes\n')
        process_file(stats, EVENT_STATS_INDEX, self.raw, self.routed, SCHEMA)
        process_file(rounds, ROUNDS_INDEX, self.raw, self.routed, SCHEMA)
        with sqlite3.connect(self.raw / 'mappings.sqlite3') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM event_mapping').fetchone()[0], 1)
            self.assertEqual(db.execute('''SELECT calendar_year,rounds_available,event_stats_available
                                          FROM event_lookup ORDER BY calendar_year''').fetchall(),
                             [(2025, 1, 0), (2026, 1, 1)])
        result = lookup(self.raw / 'mappings.sqlite3', 'pga', 2026, event_id='60')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['api_path'],
                         '/historical-raw-data/rounds?tour=pga&event_id=60&year=2026&file_format=csv')

    def test_name_without_id_is_unresolved_and_distinct_ids_do_not_merge(self):
        source = self.write_raw('player_list', 'upload.csv',
                                'dg_id,player_name,pga_tour_id\n1,Alex,\n2,Alex,\n,Unknown,\n')
        process_file(source, PLAYER, self.raw, self.routed, SCHEMA)
        with sqlite3.connect(self.raw / 'mappings.sqlite3') as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM player_mapping').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT row_number FROM unresolved_mapping').fetchone()[0], 4)

    def test_conflicting_provider_id_preserves_raw_and_previous_mapping(self):
        first = self.write_raw('player_list', 'first.csv', 'dg_id,player_name,pga_tour_id\n1,Alex,42\n')
        second = self.write_raw('player_list', 'second.csv', 'dg_id,player_name,pga_tour_id\n1,Alex,99\n')
        process_file(first, PLAYER, self.raw, self.routed, SCHEMA)
        with self.assertRaisesRegex(ValueError, 'Conflicting PGA TOUR ID'):
            process_file(second, PLAYER, self.raw, self.routed, SCHEMA)
        self.assertTrue(second.exists())
        self.assertEqual(len(list((self.routed / 'players').glob('*.csv'))), 1)

    def test_manual_inbox_scan_routes_file(self):
        path = self.write_raw('player_list', 'manual.csv', 'dg_id,player_name\n7,Bob\n')
        os.utime(path, (1, 1))  # Simulate a completed upload older than the quiet period.
        results = process_pending([PLAYER], self.raw, self.routed, SCHEMA)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['status'], 'processed')

    def test_api_download_routes_and_maps_in_one_run(self):
        class CsvResponse:
            headers = {'Content-Type': 'text/csv'}
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return b'dg_id,player_name\n7,Bob\n'

        entry = {**PLAYER, 'path': '/get-player-list', 'params': {}}
        config = self.root / 'config.json'
        config.write_text(json.dumps({'datasets': [entry]}))
        result = ingest(config, self.raw, 'test-key', opener=lambda *a, **k: CsvResponse(),
                        routed_root=self.routed, schema_path=SCHEMA, sleep=lambda _: None)
        self.assertTrue(result['success'])
        self.assertTrue(Path(result['results'][0]['file_path']).exists())
        self.assertTrue(Path(result['results'][0]['routed_file']).exists())
        with sqlite3.connect(self.raw / 'mappings.sqlite3') as db:
            self.assertEqual(db.execute('SELECT name FROM player_mapping').fetchone()[0], 'Bob')

if __name__ == '__main__':
    unittest.main()
