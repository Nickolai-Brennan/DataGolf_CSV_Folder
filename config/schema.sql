-- Local IDs never reuse external provider IDs. Source IDs remain text to retain leading zeroes.
CREATE TABLE IF NOT EXISTS player_mapping (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    datagolf_id TEXT NOT NULL UNIQUE,
    pgatour_id TEXT UNIQUE,
    source_dataset TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS course_mapping (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    datagolf_id TEXT NOT NULL UNIQUE,
    pgatour_id TEXT UNIQUE,
    source_dataset TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

-- A tournament series can appear in multiple seasons; season is observation data,
-- not part of its stable mapping key. Tour scopes the provider's event identifier.
CREATE TABLE IF NOT EXISTS event_mapping (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    datagolf_id TEXT NOT NULL,
    pgatour_id TEXT,
    tour TEXT NOT NULL,
    source_dataset TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE (datagolf_id, tour),
    UNIQUE (pgatour_id, tour)
);

CREATE TABLE IF NOT EXISTS processed_files (
    raw_path TEXT PRIMARY KEY,
    dataset TEXT NOT NULL,
    entity TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    output_path TEXT NOT NULL UNIQUE,
    row_count INTEGER NOT NULL,
    processed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS unresolved_mapping (
    raw_path TEXT NOT NULL,
    table_name TEXT NOT NULL,
    row_number INTEGER NOT NULL,
    reason TEXT NOT NULL,
    PRIMARY KEY (raw_path, table_name, row_number)
);
