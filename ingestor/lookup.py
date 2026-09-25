"""Find event IDs and the exact rounds endpoint parameters without exposing API keys."""
import argparse
import json
import sqlite3
from pathlib import Path
from urllib.parse import urlencode


def lookup(database, tour, year, kind='rounds', event_id=None, limit=20):
    if kind not in ('rounds', 'event_stats'):
        raise ValueError('kind must be rounds or event_stats')
    if not 1900 <= int(year) <= 2100 or not 1 <= limit <= 1000:
        raise ValueError('Invalid year or limit')
    if not Path(database).is_file():
        raise FileNotFoundError('Mapping database not found; run event index ingestion first')
    column = 'rounds_available' if kind == 'rounds' else 'event_stats_available'
    where = f'tour = ? AND calendar_year = ? AND {column} = 1'
    params = [tour, year]
    if event_id is not None:
        where += ' AND event_id = ?'
        params.append(str(event_id))
    with sqlite3.connect(f'file:{Path(database).resolve()}?mode=ro', uri=True) as db:
        rows = db.execute(f'''SELECT event_id, event_name, event_date, tour, calendar_year,
                            sg_categories, traditional_stats FROM event_lookup
                            WHERE {where} ORDER BY event_date DESC, event_name LIMIT ?''',
                          (*params, limit)).fetchall()
    endpoint = ('/historical-raw-data/rounds' if kind == 'rounds'
                else '/historical-event-data/events')
    return [
        {'event_id': item[0], 'event_name': item[1], 'date': item[2],
         'tour': item[3], 'calendar_year': item[4],
         'sg_categories': item[5], 'traditional_stats': item[6],
         # API key intentionally omitted; the downloader adds it at runtime.
         'api_path': endpoint + '?' + urlencode({'tour': item[3], 'event_id': item[0],
                                                 'year': item[4], 'file_format': 'csv'})}
        for item in rows
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', default='data/mappings.sqlite3')
    parser.add_argument('--tour', default='pga')
    parser.add_argument('--year', type=int, required=True, help='Calendar year, not season')
    parser.add_argument('--kind', choices=('rounds', 'event_stats'), default='rounds')
    parser.add_argument('--event-id')
    parser.add_argument('--limit', type=int, default=20)
    args = parser.parse_args()
    print(json.dumps(lookup(args.database, args.tour, args.year, args.kind,
                            args.event_id, args.limit), indent=2))


if __name__ == '__main__':
    main()
