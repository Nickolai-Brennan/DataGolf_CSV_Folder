# Data Golf ingestion

Self-hosted n8n schedules a Python service that downloads Data Golf's documented CSV feeds and stores immutable UTC snapshots. This is a standalone ingestion starter, separate from the golf simulation database and CaddyStats repositories.

## Quick start

1. Copy `.env.example` to `.env`. Set your current Data Golf API key and a newly generated n8n encryption key. Keep `.env` private. Rotate the key and password previously pasted into chat.
2. Run `docker compose up -d` from this directory.
3. Open `http://localhost:5678`, finish the n8n first-run setup, and import `n8n/datagolf_ingestion.json` via **Import from File**.
4. Run the Manual Trigger and inspect the result. Then activate the workflow to schedule it daily at 06:00 America/Detroit.
5. Check `data/player_list/YYYY/MM/DD/`, `data/pga_schedule/YYYY/MM/DD/`, and `data/ingestion_runs.jsonl`.

On a remote server, use an SSH tunnel or authenticated reverse proxy for the localhost-bound n8n UI. The ingestion service is reachable only inside Docker Compose.

## Configuration

Edit `config/datasets.json` and restart the ingestor (`docker compose restart ingestor`). Each entry needs `name` (lowercase letters, digits and underscores), documented API `path`, optional query `params`, and `enabled`. Query authentication and `file_format=csv` are added by the service. The sample includes the player list and PGA schedule feeds.

The archive preserves Data Golf's raw CSV columns. `ingestion_runs.jsonl` records retrieval time, row count, file path and SHA-256 hash. Source observation dates, seasons and rounds must be interpreted from each feed's columns downstream; retrieval time is never substituted for an observation date. Identical responses are intentionally retained as distinct snapshots in this starter.

The service retries temporary failures, validates CSV shape and records failures without the authenticated URL. n8n receives an HTTP failure if any enabled feed fails. No webpage login or browser extraction is implemented yet; supply the exact pages and fields before adding a browser adapter.

## Test

```bash
cd datagolf-ingestion
python3 -m unittest discover -s tests -v
python3 -m json.tool n8n/datagolf_ingestion.json >/dev/null
docker compose config --quiet
```

`docker compose config` needs values in `.env`. Tests run offline. Live runs require an API subscription and network access. Review Data Golf's license terms before commercial use or redistribution.

## Sources

- Data Golf API documentation: https://datagolf.com/api-access
- n8n workflow import/export: https://docs.n8n.io/build/manage-workflows/export-and-import/
