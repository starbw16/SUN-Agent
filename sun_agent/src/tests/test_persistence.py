"""Tests for store silo creation, schema, and upsert logic."""
import sqlite3
import tempfile
import os
import pytest

from ..persistence.schema import init_schema
from ..persistence.repository import (
    log_ingestion_start, log_ingestion_complete,
    upsert_client, insert_visit, insert_schedule_slot,
    insert_risk_snapshot,
)


@pytest.fixture
def db():
    """In-memory SQLite with schema applied."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    from ..persistence.schema import DDL
    conn.executescript(DDL)
    # Seed a store
    conn.execute("INSERT INTO stores (store_id, store_name) VALUES ('store1', 'Test Store')")
    conn.commit()
    yield conn
    conn.close()


def test_schema_creates_tables(db):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    expected = {
        "stores", "store_config", "ingestion_log", "clients", "client_visits",
        "appointments", "appointment_events", "provider_schedule_slots",
        "client_risk_snapshot", "forecast_daily_channels",
    }
    assert expected.issubset(tables)


def test_ingestion_log_dedup(db):
    db.execute("INSERT INTO store_config (store_id) VALUES ('store1')")
    db.commit()
    id1 = log_ingestion_start(db, "store1", "file.xlsx", "clients_with_service", "abc123")
    id2 = log_ingestion_start(db, "store1", "file2.xlsx", "clients_with_service", "abc123")
    assert id1 is not None
    assert id2 is None  # same hash → duplicate


def test_client_upsert_creates_new(db):
    key = upsert_client(db, "store1", "John Doe", "john doe", "5551234567",
                        visit_date="2024-04-01", service_description="Kids Cut")
    db.commit()
    row = db.execute("SELECT * FROM clients WHERE client_key=?", (key,)).fetchone()
    assert row is not None
    assert row["client_name_normalized"] == "john doe"
    assert row["total_visits"] == 1
    assert row["last_seen_date"] == "2024-04-01"


def test_client_upsert_increments_visits(db):
    key = upsert_client(db, "store1", "Jane Smith", "jane smith", "5559876543",
                        visit_date="2024-03-01")
    db.commit()
    upsert_client(db, "store1", "Jane Smith", "jane smith", "5559876543",
                  visit_date="2024-04-01")
    db.commit()
    row = db.execute("SELECT * FROM clients WHERE client_key=?", (key,)).fetchone()
    assert row["total_visits"] == 2
    assert row["last_seen_date"] == "2024-04-01"
    assert row["first_seen_date"] == "2024-03-01"


def test_client_key_isolation_by_store(db):
    db.execute("INSERT INTO stores (store_id, store_name) VALUES ('store2', 'Store 2')")
    db.commit()
    k1 = upsert_client(db, "store1", "John Doe", "john doe", "5551234567")
    k2 = upsert_client(db, "store2", "John Doe", "john doe", "5551234567")
    db.commit()
    assert k1 != k2


def test_household_key_set(db):
    key = upsert_client(db, "store1", "Child A", "child a", "5551234567")
    upsert_client(db, "store1", "Child B", "child b", "5551234567")
    db.commit()
    rows = db.execute(
        "SELECT household_key FROM clients WHERE store_id='store1' AND primary_phone='5551234567'"
    ).fetchall()
    household_keys = {r["household_key"] for r in rows}
    assert len(household_keys) == 1  # same household


def test_visit_dedup(db):
    ingestion_id = "test_ingest_01"
    db.execute(
        "INSERT INTO ingestion_log (ingestion_id, store_id, source_filename, report_type, file_hash) VALUES (?, 'store1', 'f.xlsx', 'clients_with_service', 'h1')",
        (ingestion_id,),
    )
    db.commit()
    key = upsert_client(db, "store1", "John Doe", "john doe", "5551234567")
    db.commit()
    v1 = insert_visit(db, "store1", key, "2024-04-01", "Haircut", "Kids Cut",
                      "clients_with_service", ingestion_id)
    v2 = insert_visit(db, "store1", key, "2024-04-01", "Haircut", "Kids Cut",
                      "clients_with_service", ingestion_id)
    db.commit()
    count = db.execute("SELECT COUNT(*) FROM client_visits WHERE client_key=?", (key,)).fetchone()[0]
    assert count == 1  # idempotent


def _seed_ingestion(db, ingestion_id="ingest01"):
    db.execute(
        """INSERT OR IGNORE INTO ingestion_log
           (ingestion_id, store_id, source_filename, report_type, file_hash)
           VALUES (?, 'store1', 'f.xlsx', 'cancel_no_show', 'hash_test')""",
        (ingestion_id,),
    )
    db.commit()


def test_risk_snapshot(db):
    _seed_ingestion(db)
    key = upsert_client(db, "store1", "Bad Actor", "bad actor", None)
    db.commit()
    insert_risk_snapshot(db, "store1", key, "2024-04-01", 4, "ingest01")
    db.commit()
    row = db.execute("SELECT * FROM client_risk_snapshot WHERE client_key=?", (key,)).fetchone()
    assert row["risk_band"] == "high"
    assert row["cancel_no_show_count"] == 4


def test_schedule_slot_insert(db):
    _seed_ingestion(db)
    insert_schedule_slot(
        db, "store1", "Jessica", "JESS", "2024-04-01", "09:00", "open",
        0, None, None, None, None, "stylist_daily_schedule", "ingest01"
    )
    db.commit()
    row = db.execute("SELECT * FROM provider_schedule_slots WHERE store_id='store1'").fetchone()
    assert row["slot_state"] == "open"
    assert row["provider_name"] == "Jessica"
