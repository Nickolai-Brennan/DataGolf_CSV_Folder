# Data Golf ingestion

Self-hosted n8n schedules a Python service that downloads Data Golf's documented CSV feeds and stores immutable UTC snapshots. Phase 2 routes raw files to `CSV Files/` and maintains source ID mappings in SQLite. This is a standalone ingestion project, separate from the golf simulation database and CaddyStats repositories.

## Quick start

1. Copy `.env.example` to `.env`. Set your current Data Golf API key and a newly generated n8n encryption key. Keep `.env` private. Rotate the key and password previously pasted into chat.
2. Run `docker compose up -d` from this directory.
3. Open `http://localhost:5678`, finish the n8n first-run setup, and import `n8n/datagolf_ingestion.json` via **Import from File**.
4. Run the Manual Trigger and inspect the result. Then activate the workflow to schedule it daily at 06:00 America/Detroit.
5. Check `data/player_list/YYYY/MM/DD/`, `data/pga_schedule/YYYY/MM/DD/`, `CSV Files/players/`, `CSV Files/events/`, `data/mappings.sqlite3`, and `data/ingestion_runs.jsonl`.

On a remote server, use an SSH tunnel or authenticated reverse proxy for the localhost-bound n8n UI. The ingestion service is reachable only inside Docker Compose.

## Configuration

Edit `config/datasets.json` and restart the ingestor (`docker compose restart ingestor`). Each entry needs `name` (lowercase letters, digits and underscores), documented API `path`, optional query `params`, and `enabled`. Query authentication and `file_format=csv` are added by the service. The sample includes the player list and PGA schedule feeds.

The archive preserves Data Golf's raw CSV columns. `ingestion_runs.jsonl` records retrieval time, row count, file path and SHA-256 hash. Source observation dates, seasons and rounds must be interpreted from each feed's columns downstream; retrieval time is never substituted for an observation date. Identical responses are intentionally retained as distinct snapshots in this starter.

## Phase 2: raw inbox, routing and mappings

`data/` remains the raw landing area. After an API download, the service atomically publishes the raw CSV and immediately copies it to the configured destination. A background scanner runs every 15 seconds to retry unprocessed API files and handle manual uploads. Put a complete manual CSV at `data/inbox/<dataset_name>/<your_file>.csv` (for example, `data/inbox/player_list/players_2026-09-25.csv`). Upload via a temporary filename and rename to `.csv` when complete; the scanner also waits at least two seconds after the file's last modification. The file must have the headers configured for that dataset in `config/datasets.json`.

Routing uses each dataset's `entity` setting. `player_list` goes to `CSV Files/players/`, `pga_schedule` goes to `CSV Files/events/`, and a manual file at `data/inbox/course_catalog/<file>.csv` goes to `CSV Files/courses/`. The copied filename contains the entity, dataset, raw filename and a short content hash. The original remains untouched in `data/`. The schedule also populates course mappings when it includes course IDs, without duplicating an event CSV into the courses folder.

The mapping database is `data/mappings.sqlite3`, created from `config/schema.sql` on the first processed file. Its `player_mapping`, `course_mapping` and `event_mapping` tables each contain a local `id`, `name`, `datagolf_id`, nullable `pgatour_id`, source dataset, and first/last seen timestamps. Events also have `tour`, since provider event IDs can require tour context. `processed_files` tracks raw-to-copied paths; `unresolved_mapping` records source rows missing a name or Data Golf ID. Source IDs remain text, preserving leading zeroes. The processor never merges different IDs merely because the names match. A conflicting PGA TOUR ID fails processing, retains the raw file, and can be retried after correction.

The `name_columns`, `datagolf_id_columns` and `pgatour_id_columns` lists in the dataset config are candidate source headers. Live CSV headers were checked for the player list, PGA schedule and two event indexes in September 2026; check again if upstream schemas change. The schedule uses `course_key` as the Data Golf course ID. No PGA TOUR ID is invented when Data Golf omits it. Future PGA TOUR datasets can be reconciled using verified external IDs; name-only rows stay unresolved.

## Historical event lookup

Two PGA event indexes are downloaded into `data/` and copied to `CSV Files/events/`:

| Dataset | Event list endpoint | Used to look up |
| --- | --- | --- |
| `historical_event_stats_index` | `/historical-event-data/event-list` | `/historical-event-data/events` |
| `historical_rounds_index` | `/historical-raw-data/event-list` | `/historical-raw-data/rounds` |

The indexes contain `tour`, `calendar_year`, `date`, `event_name`, and `event_id`. The rounds index also reports `sg_categories` and `traditional_stats`. Every index row updates `event_lookup` in `data/mappings.sqlite3`; its key is `(tour, event_id, calendar_year)`, since a tournament ID can recur in later calendar years. The two endpoint families have different coverage. On September 25, 2026 the first returned 84 PGA entries for 2025–2026, while the second returned 1,086 PGA entries over a longer history. These are live observations and may change.

After a run, look up an event and its parameterized endpoint without printing your API key:

```bash
python3 -m ingestor.lookup --year 2026 --tour pga --event-id 60 --kind rounds
python3 -m ingestor.lookup --year 2026 --tour pga --kind event_stats --limit 10
```

The returned `api_path` includes `tour`, `event_id`, `year` (calendar year), and `file_format=csv`. The ingestor adds the key only at request time. **Rounds are not downloaded automatically.** The supplied key successfully retrieved both event lists but a test request to `/historical-raw-data/rounds` returned HTTP 403 with a historical-data subscription message. Access must be enabled before implementing or running a rounds downloader. The event stats data endpoint is likewise only a lookup target here, not an automatic download.

SQLite is local to this deployment. Do not run multiple ingestor instances against the same archive. For downstream use, query the three mapping tables directly or export them explicitly; database and generated CSV files are excluded from Git.

The service retries temporary failures, validates CSV shape and records failures without the authenticated URL. n8n receives an HTTP failure if any enabled feed fails. No webpage login or browser extraction is implemented yet; supply the exact pages and fields before adding a browser adapter.

## Test

```bash
cd DataGolf_CSV_Folder
python3 -m unittest discover -s tests -v
python3 -m json.tool n8n/datagolf_ingestion.json >/dev/null
docker compose config --quiet
```

`docker compose config` needs values in `.env`. Tests run offline. Live runs require an API subscription and network access. Review Data Golf's license terms before commercial use or redistribution.

## Sources

- Data Golf API documentation: https://datagolf.com/api-access
- n8n workflow import/export: https://docs.n8n.io/build/manage-workflows/export-and-import/
