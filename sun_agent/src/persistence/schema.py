"""
SQLite schema creation for a store silo database.
"""
import sqlite3


DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS stores (
    store_id            TEXT PRIMARY KEY,
    store_name          TEXT NOT NULL,
    source_store_name   TEXT,
    created_at          TEXT DEFAULT (datetime('now')),
    updated_at          TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS store_config (
    store_id                TEXT PRIMARY KEY REFERENCES stores(store_id),
    booking_url             TEXT,
    utilization_threshold   REAL DEFAULT 0.80,
    utilization_tier        TEXT DEFAULT 'mid',
    brief_frequency         TEXT DEFAULT 'daily',
    retention_windows_json  TEXT DEFAULT '[28, 42, 56]',
    risk_thresholds_json    TEXT DEFAULT '{"high": 3, "medium": 1}',
    owner_phone             TEXT,
    owner_email             TEXT,
    twilio_number           TEXT,
    timezone                TEXT DEFAULT 'America/New_York',
    active                  INTEGER DEFAULT 1,
    updated_at              TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sms_opt_outs (
    opt_out_id      TEXT PRIMARY KEY,
    store_id        TEXT NOT NULL REFERENCES stores(store_id),
    phone           TEXT NOT NULL,
    opted_out_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(store_id, phone)
);

CREATE TABLE IF NOT EXISTS brief_log (
    brief_id        TEXT PRIMARY KEY,
    store_id        TEXT NOT NULL REFERENCES stores(store_id),
    sent_at         TEXT DEFAULT (datetime('now')),
    brief_date      TEXT NOT NULL,
    recipient_email TEXT,
    status          TEXT DEFAULT 'sent',
    error_message   TEXT
);

CREATE TABLE IF NOT EXISTS ingestion_log (
    ingestion_id        TEXT PRIMARY KEY,
    store_id            TEXT NOT NULL REFERENCES stores(store_id),
    source_filename     TEXT NOT NULL,
    report_type         TEXT NOT NULL,
    file_hash           TEXT NOT NULL,
    report_start_date   TEXT,
    report_end_date     TEXT,
    generated_at        TEXT,
    ingested_at         TEXT DEFAULT (datetime('now')),
    status              TEXT DEFAULT 'pending',
    row_count_raw       INTEGER DEFAULT 0,
    row_count_loaded    INTEGER DEFAULT 0,
    error_message       TEXT,
    UNIQUE(store_id, file_hash)
);

CREATE TABLE IF NOT EXISTS clients (
    client_key              TEXT NOT NULL,
    store_id                TEXT NOT NULL REFERENCES stores(store_id),
    client_name_raw         TEXT,
    client_name_normalized  TEXT NOT NULL,
    primary_phone           TEXT,
    household_key           TEXT,
    first_seen_date         TEXT,
    last_seen_date          TEXT,
    total_visits            INTEGER DEFAULT 0,
    last_service_description TEXT,
    preferred_contact_method TEXT,
    created_at              TEXT DEFAULT (datetime('now')),
    updated_at              TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (client_key, store_id)
);

CREATE TABLE IF NOT EXISTS client_visits (
    visit_id            TEXT PRIMARY KEY,
    store_id            TEXT NOT NULL REFERENCES stores(store_id),
    client_key          TEXT NOT NULL,
    visit_date          TEXT,
    service_category    TEXT,
    service_description TEXT,
    source_report       TEXT,
    ingestion_id        TEXT REFERENCES ingestion_log(ingestion_id)
);

CREATE TABLE IF NOT EXISTS appointments (
    appointment_key         TEXT PRIMARY KEY,
    store_id                TEXT NOT NULL REFERENCES stores(store_id),
    client_key              TEXT,
    provider_code           TEXT,
    appointment_date        TEXT,
    start_time              TEXT,
    end_time                TEXT,
    service_code            TEXT,
    service_description     TEXT,
    status_raw              TEXT,
    booking_source          TEXT,
    preferred_contact_method TEXT,
    source_report           TEXT,
    ingestion_id            TEXT REFERENCES ingestion_log(ingestion_id)
);

CREATE TABLE IF NOT EXISTS appointment_events (
    event_id            TEXT PRIMARY KEY,
    store_id            TEXT NOT NULL REFERENCES stores(store_id),
    appointment_key     TEXT,
    event_type          TEXT,
    event_timestamp_raw TEXT,
    actor_code          TEXT,
    comments            TEXT,
    remarks             TEXT,
    source_report       TEXT,
    ingestion_id        TEXT REFERENCES ingestion_log(ingestion_id)
);

CREATE TABLE IF NOT EXISTS provider_schedule_slots (
    slot_id             TEXT PRIMARY KEY,
    store_id            TEXT NOT NULL REFERENCES stores(store_id),
    provider_name       TEXT,
    provider_code       TEXT,
    slot_date           TEXT,
    slot_time           TEXT,
    slot_state          TEXT,
    is_appt_start       INTEGER DEFAULT 0,
    client_name_raw     TEXT,
    service_description TEXT,
    status_raw          TEXT,
    notes_raw           TEXT,
    source_report       TEXT,
    ingestion_id        TEXT REFERENCES ingestion_log(ingestion_id)
);

CREATE TABLE IF NOT EXISTS client_risk_snapshot (
    snapshot_id         TEXT PRIMARY KEY,
    store_id            TEXT NOT NULL REFERENCES stores(store_id),
    client_key          TEXT,
    snapshot_date       TEXT,
    cancel_no_show_count INTEGER,
    risk_score          REAL,
    risk_band           TEXT,
    reason_json         TEXT,
    ingestion_id        TEXT REFERENCES ingestion_log(ingestion_id)
);

CREATE TABLE IF NOT EXISTS forecast_daily_channels (
    forecast_id         TEXT PRIMARY KEY,
    store_id            TEXT NOT NULL REFERENCES stores(store_id),
    forecast_date       TEXT,
    booking_channel     TEXT,
    booking_count       INTEGER,
    source_report       TEXT,
    ingestion_id        TEXT REFERENCES ingestion_log(ingestion_id)
);

CREATE TABLE IF NOT EXISTS provider_timesheets (
    timesheet_id        TEXT PRIMARY KEY,
    store_id            TEXT NOT NULL REFERENCES stores(store_id),
    provider_name_raw   TEXT,
    provider_name_norm  TEXT NOT NULL,
    work_date           TEXT NOT NULL,
    time_in             TEXT,
    time_out            TEXT,
    hours_worked        REAL,
    is_complete         INTEGER DEFAULT 1,
    source_report       TEXT,
    ingestion_id        TEXT REFERENCES ingestion_log(ingestion_id),
    UNIQUE(store_id, provider_name_norm, work_date)
);

CREATE TABLE IF NOT EXISTS review_requests (
    request_id      TEXT PRIMARY KEY,
    store_id        TEXT NOT NULL REFERENCES stores(store_id),
    client_key      TEXT,
    phone           TEXT NOT NULL,
    client_name     TEXT,
    service_date    TEXT NOT NULL,
    sent_at         TEXT DEFAULT (datetime('now')),
    response_text   TEXT,
    response_at     TEXT,
    outcome         TEXT DEFAULT 'pending',
    alert_sent      INTEGER DEFAULT 0,
    UNIQUE(store_id, phone, service_date)
);

CREATE INDEX IF NOT EXISTS idx_clients_store ON clients(store_id);
CREATE INDEX IF NOT EXISTS idx_clients_phone ON clients(primary_phone);
CREATE INDEX IF NOT EXISTS idx_visits_client ON client_visits(client_key, store_id);
CREATE INDEX IF NOT EXISTS idx_visits_date ON client_visits(visit_date);
CREATE INDEX IF NOT EXISTS idx_appt_date ON appointments(appointment_date);
CREATE INDEX IF NOT EXISTS idx_slots_date ON provider_schedule_slots(slot_date, slot_state);
CREATE INDEX IF NOT EXISTS idx_risk_client ON client_risk_snapshot(client_key, store_id);
"""


def init_schema(db_path: str) -> sqlite3.Connection:
    """Create all tables in the store's SQLite database. Idempotent."""
    conn = sqlite3.connect(db_path)
    conn.executescript(DDL)
    conn.commit()
    return conn
