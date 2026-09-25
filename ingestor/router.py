"""Route raw CSV snapshots and maintain conservative source-ID mapping tables."""
import csv
import hashlib
import io
import os
import re
import shutil
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

ENTITIES = {'players': 'player_mapping', 'courses': 'course_mapping', 'events': 'event_mapping'}
_lock = threading.Lock()  # Serializes the watcher and API request in this process.


def _field(row, candidates):
    for candidate in candidates:
        value = row.get(candidate)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _update_mapping(db, mapping, row, raw_path, row_number, dataset, timestamp):
    entity = mapping['entity']
    table = ENTITIES[entity]  # Fixed allowlist prevents SQL identifier injection.
    name = _field(row, mapping['name_columns'])
    dg_id = _field(row, mapping['datagolf_id_columns'])
    pga_id = _field(row, mapping.get('pgatour_id_columns', []))
    if not name or not dg_id:
        db.execute('INSERT OR IGNORE INTO unresolved_mapping VALUES (?, ?, ?, ?)',
                   (raw_path, table, row_number, 'missing name or datagolf_id'))
        return False
    tour = mapping.get('tour', '') if entity == 'events' else None
    selector = f'SELECT id, pgatour_id FROM {table} WHERE datagolf_id = ?'
    values = (dg_id,)
    if entity == 'events':
        selector += ' AND tour = ?'
        values += (tour,)
    existing = db.execute(selector, values).fetchone()
    if existing:
        if pga_id and existing[1] and pga_id != existing[1]:
            raise ValueError(f'Conflicting PGA TOUR ID in {table} at row {row_number}')
        db.execute(f'''UPDATE {table} SET name = ?, pgatour_id = COALESCE(pgatour_id, ?),
                    source_dataset = ?, last_seen_at = ? WHERE id = ?''',
                   (name, pga_id, dataset, timestamp, existing[0]))
    elif entity == 'events':
        db.execute(f'''INSERT INTO {table}
                    (name, datagolf_id, pgatour_id, tour, source_dataset, first_seen_at, last_seen_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)''',
                   (name, dg_id, pga_id, tour, dataset, timestamp, timestamp))
    else:
        db.execute(f'''INSERT INTO {table}
                    (name, datagolf_id, pgatour_id, source_dataset, first_seen_at, last_seen_at)
                    VALUES (?, ?, ?, ?, ?, ?)''',
                   (name, dg_id, pga_id, dataset, timestamp, timestamp))
    return True


def _update_event_lookup(db, dataset, row, row_number, timestamp):
    """Track which endpoint family lists each tournament edition."""
    kind = dataset.get('event_index_kind')
    if kind not in ('rounds', 'event_stats'):
        return
    tour = (row.get('tour') or '').strip().lower()
    event_id = (row.get('event_id') or '').strip()
    name = (row.get('event_name') or '').strip()
    year_text = (row.get('calendar_year') or '').strip()
    if not (tour and event_id and name and year_text.isdigit() and len(year_text) == 4):
        raise ValueError(f'Invalid event lookup row {row_number}')
    year = int(year_text)
    if not 1900 <= year <= 2100:
        raise ValueError(f'Invalid calendar year at row {row_number}')
    if tour != dataset.get('params', {}).get('tour', tour):
        raise ValueError(f'Unexpected tour at row {row_number}')
    db.execute('''INSERT INTO event_lookup
        (tour, event_id, calendar_year, event_name, event_date,
         rounds_available, event_stats_available, sg_categories, traditional_stats, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (tour, event_id, calendar_year) DO UPDATE SET
            event_name = excluded.event_name,
            event_date = COALESCE(excluded.event_date, event_lookup.event_date),
            rounds_available = MAX(event_lookup.rounds_available, excluded.rounds_available),
            event_stats_available = MAX(event_lookup.event_stats_available, excluded.event_stats_available),
            sg_categories = COALESCE(excluded.sg_categories, event_lookup.sg_categories),
            traditional_stats = COALESCE(excluded.traditional_stats, event_lookup.traditional_stats),
            last_seen_at = excluded.last_seen_at''',
        (tour, event_id, year, name, (row.get('date') or '').strip() or None,
         int(kind == 'rounds'), int(kind == 'event_stats'),
         (row.get('sg_categories') or '').strip() or None,
         (row.get('traditional_stats') or '').strip() or None, timestamp))


def process_file(raw_file, dataset, archive_root, routed_root, schema_path):
    """Copy one stable raw CSV; commit file registration and mappings together.

    Repeated calls are idempotent. A failed mapping rolls back database writes and
    removes the copied file, leaving the raw snapshot available for a retry.
    """
    raw = Path(raw_file).resolve()
    archive = Path(archive_root).resolve()
    target_root = Path(routed_root).resolve()
    if not raw.is_relative_to(archive) or raw.suffix.lower() != '.csv' or raw.is_symlink():
        raise ValueError('Input must be a regular CSV within the raw archive')
    entity = dataset['entity']
    if entity not in ENTITIES or not re.fullmatch(r'[a-z][a-z0-9_]*', dataset['name']):
        raise ValueError('Unknown entity or invalid dataset name')
    mappings = dataset.get('mappings', [])
    if not mappings or any(m.get('entity') not in ENTITIES for m in mappings):
        raise ValueError('Dataset must declare mapping definitions')
    with _lock:
        # Read file fully before processing; the API downloader publishes it atomically.
        data = raw.read_bytes()
        try:
            content = data.decode('utf-8-sig')
        except UnicodeDecodeError as exc:
            raise ValueError('Input is not UTF-8 CSV') from exc
        reader = csv.DictReader(io.StringIO(content, newline=''))
        headers = reader.fieldnames or []
        if not headers or len(set(headers)) != len(headers) or any(not h.strip() for h in headers):
            raise ValueError('Invalid CSV headers')
        rows = list(reader)
        if not rows or any(None in row or any(value is None for value in row.values()) for row in rows):
            raise ValueError('Empty or inconsistent CSV rows')
        active_mappings = []
        for mapping in mappings:
            available = (set(mapping['name_columns']).intersection(headers)
                         and set(mapping['datagolf_id_columns']).intersection(headers))
            if not available and mapping.get('required', True):
                raise ValueError(f"Missing required {mapping['entity']} mapping columns")
            if available:
                active_mappings.append(mapping)
        if dataset.get('event_index_kind') and not {'tour', 'event_id', 'event_name', 'calendar_year'}.issubset(headers):
            raise ValueError('Missing required event lookup columns')
        target = target_root / entity
        target.mkdir(parents=True, exist_ok=True)
        # Existing ingestion names are timestamped; manual files gain a UTC timestamp.
        stem = re.sub(r'[^a-zA-Z0-9_-]+', '_', raw.stem).strip('_')[:100]
        if not stem:
            raise ValueError('Invalid raw CSV filename')
        digest = hashlib.sha256(data).hexdigest()
        filename = f"{entity}_{dataset['name']}_{stem}_{digest[:12]}.csv"
        output = target / filename
        db_path = archive / 'mappings.sqlite3'
        with sqlite3.connect(db_path, timeout=30) as db:
            db.execute('PRAGMA busy_timeout = 30000')
            db.executescript(Path(schema_path).read_text(encoding='utf-8'))
            existing = db.execute('SELECT output_path FROM processed_files WHERE raw_path = ?', (str(raw),)).fetchone()
            if existing:
                if not Path(existing[0]).exists():
                    raise ValueError('Registered output file is missing; repair before retrying')
                return {'status': 'already_processed', 'output_path': existing[0]}
            if output.exists():
                raise ValueError('Output filename collision')
            temporary = target / (filename + '.tmp')
            try:
                # Keep the destination on the same filesystem for atomic publish.
                with raw.open('rb') as source, temporary.open('xb') as dest:
                    shutil.copyfileobj(source, dest)
                db.execute('BEGIN IMMEDIATE')
                timestamp = datetime.now(timezone.utc).isoformat()
                counts = {kind: 0 for kind in ENTITIES}
                for row_number, row in enumerate(rows, start=2):
                    for mapping in active_mappings:
                        if _update_mapping(db, mapping, row, str(raw), row_number, dataset['name'], timestamp):
                            counts[mapping['entity']] += 1
                    _update_event_lookup(db, dataset, row, row_number, timestamp)
                os.replace(temporary, output)
                db.execute('''INSERT INTO processed_files
                    (raw_path, dataset, entity, sha256, output_path, row_count, processed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)''',
                    (str(raw), dataset['name'], entity, digest, str(output), len(rows), timestamp))
                db.commit()
            except Exception:
                db.rollback()
                temporary.unlink(missing_ok=True)
                output.unlink(missing_ok=True)
                raise
        return {'status': 'processed', 'output_path': str(output), 'row_count': len(rows), 'mapping_rows': counts}


def process_pending(datasets, archive_root, routed_root, schema_path):
    """Scan both API archives and manual data/inbox/<dataset> uploads."""
    root = Path(archive_root).resolve()
    results = []
    for dataset in datasets:
        if not dataset.get('enabled', False):
            continue
        name = dataset['name']
        if not re.fullmatch(r'[a-z][a-z0-9_]*', name):
            raise ValueError('Invalid dataset name')
        candidates = list((root / name).rglob('*.csv')) + list((root / 'inbox' / name).glob('*.csv'))
        for path in sorted(candidates):
            # Manual uploads need two seconds of unchanged mtime; API files are atomic.
            if path.is_relative_to(root / 'inbox') and datetime.now(timezone.utc).timestamp() - path.stat().st_mtime < 2:
                continue
            try:
                result = process_file(path, dataset, root, routed_root, schema_path)
                if result['status'] == 'processed':
                    results.append({'raw_path': str(path), **result})
            except Exception as exc:
                results.append({'raw_path': str(path), 'status': 'failed', 'error': str(exc)})
    return results
