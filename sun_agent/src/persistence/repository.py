"""
Idempotent upsert logic for all tables.
All writes go through this module — never raw SQL in parsers or pipeline.
"""
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from ..normalization.normalizer import make_client_key, make_household_key


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Ingestion log ────────────────────────────────────────────────────────────

def log_ingestion_start(conn: sqlite3.Connection, store_id: str, filename: str,
                        report_type: str, file_hash: str,
                        report_start_date=None, generated_at=None) -> Optional[str]:
    """
    Returns ingestion_id if this file hasn't been loaded before, else None (duplicate).
    Uniqueness is enforced on (store_id, file_hash).
    """
    ingestion_id = hashlib.sha256(f"{store_id}|{file_hash}".encode()).hexdigest()[:24]
    try:
        conn.execute(
            """INSERT INTO ingestion_log
               (ingestion_id, store_id, source_filename, report_type, file_hash,
                report_start_date, generated_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'in_progress')""",
            (ingestion_id, store_id, filename, report_type, file_hash,
             report_start_date, generated_at),
        )
        conn.commit()
        return ingestion_id
    except sqlite3.IntegrityError:
        return None  # duplicate


def log_ingestion_complete(conn: sqlite3.Connection, ingestion_id: str,
                           row_count_raw: int, row_count_loaded: int):
    conn.execute(
        """UPDATE ingestion_log
           SET status='complete', row_count_raw=?, row_count_loaded=?, ingested_at=?
           WHERE ingestion_id=?""",
        (row_count_raw, row_count_loaded, _now(), ingestion_id),
    )
    conn.commit()


def log_ingestion_error(conn: sqlite3.Connection, ingestion_id: str, error: str):
    conn.execute(
        """UPDATE ingestion_log SET status='error', error_message=? WHERE ingestion_id=?""",
        (error[:2000], ingestion_id),
    )
    conn.commit()


# ── Clients ──────────────────────────────────────────────────────────────────

def upsert_client(conn: sqlite3.Connection, store_id: str, client_name_raw: str,
                  client_name_normalized: str, primary_phone: Optional[str],
                  visit_date: Optional[str] = None,
                  service_description: Optional[str] = None,
                  preferred_contact: Optional[str] = None) -> str:
    client_key = make_client_key(store_id, client_name_normalized, primary_phone)
    household_key = make_household_key(store_id, primary_phone)

    existing = conn.execute(
        "SELECT client_key, first_seen_date, last_seen_date, total_visits FROM clients WHERE client_key=? AND store_id=?",
        (client_key, store_id),
    ).fetchone()

    if existing:
        first_seen = existing["first_seen_date"]
        last_seen = existing["last_seen_date"]
        total = existing["total_visits"]

        if visit_date:
            if not first_seen or visit_date < first_seen:
                first_seen = visit_date
            if not last_seen or visit_date > last_seen:
                last_seen = visit_date
            total += 1

        conn.execute(
            """UPDATE clients SET
               client_name_raw=?, primary_phone=?, household_key=?,
               first_seen_date=?, last_seen_date=?, total_visits=?,
               last_service_description=COALESCE(?, last_service_description),
               preferred_contact_method=COALESCE(?, preferred_contact_method),
               updated_at=?
               WHERE client_key=? AND store_id=?""",
            (client_name_raw, primary_phone, household_key,
             first_seen, last_seen, total,
             service_description, preferred_contact,
             _now(), client_key, store_id),
        )
    else:
        conn.execute(
            """INSERT INTO clients
               (client_key, store_id, client_name_raw, client_name_normalized,
                primary_phone, household_key, first_seen_date, last_seen_date,
                total_visits, last_service_description, preferred_contact_method)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (client_key, store_id, client_name_raw, client_name_normalized,
             primary_phone, household_key,
             visit_date, visit_date, 1 if visit_date else 0,
             service_description, preferred_contact),
        )

    return client_key


# ── Client visits ────────────────────────────────────────────────────────────

def insert_visit(conn: sqlite3.Connection, store_id: str, client_key: str,
                 visit_date: Optional[str], service_category: Optional[str],
                 service_description: Optional[str], source_report: str,
                 ingestion_id: str) -> str:
    visit_id = hashlib.sha256(
        f"{store_id}|{client_key}|{visit_date}|{service_description}".encode()
    ).hexdigest()[:24]
    conn.execute(
        """INSERT OR IGNORE INTO client_visits
           (visit_id, store_id, client_key, visit_date, service_category,
            service_description, source_report, ingestion_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (visit_id, store_id, client_key, visit_date, service_category,
         service_description, source_report, ingestion_id),
    )
    return visit_id


# ── Appointments ─────────────────────────────────────────────────────────────

def upsert_appointment(conn: sqlite3.Connection, store_id: str, client_key: Optional[str],
                       provider_code: Optional[str], appointment_date: Optional[str],
                       start_time: Optional[str], end_time: Optional[str],
                       service_description: Optional[str], status_raw: Optional[str],
                       booking_source: Optional[str], preferred_contact: Optional[str],
                       source_report: str, ingestion_id: str) -> str:
    appt_key = hashlib.sha256(
        f"{store_id}|{client_key}|{appointment_date}|{start_time}|{provider_code}".encode()
    ).hexdigest()[:24]
    conn.execute(
        """INSERT OR REPLACE INTO appointments
           (appointment_key, store_id, client_key, provider_code, appointment_date,
            start_time, end_time, service_description, status_raw, booking_source,
            preferred_contact_method, source_report, ingestion_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (appt_key, store_id, client_key, provider_code, appointment_date,
         start_time, end_time, service_description, status_raw, booking_source,
         preferred_contact, source_report, ingestion_id),
    )
    return appt_key


# ── Appointment events ───────────────────────────────────────────────────────

def insert_appointment_event(conn: sqlite3.Connection, store_id: str,
                             appointment_key: Optional[str], event_type: str,
                             timestamp_raw: Optional[str], actor_code: Optional[str],
                             comments: Optional[str], remarks: Optional[str],
                             source_report: str, ingestion_id: str) -> str:
    event_id = hashlib.sha256(
        f"{store_id}|{appointment_key}|{event_type}|{timestamp_raw}|{comments}".encode()
    ).hexdigest()[:24]
    conn.execute(
        """INSERT OR IGNORE INTO appointment_events
           (event_id, store_id, appointment_key, event_type, event_timestamp_raw,
            actor_code, comments, remarks, source_report, ingestion_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (event_id, store_id, appointment_key, event_type, timestamp_raw,
         actor_code, comments, remarks, source_report, ingestion_id),
    )
    return event_id


# ── Provider schedule slots ───────────────────────────────────────────────────

def insert_schedule_slot(conn: sqlite3.Connection, store_id: str,
                         provider_name: Optional[str], provider_code: Optional[str],
                         slot_date: Optional[str], slot_time: Optional[str],
                         slot_state: str, is_appt_start: int = 0,
                         client_name_raw: Optional[str] = None,
                         service_description: Optional[str] = None,
                         status_raw: Optional[str] = None,
                         notes_raw: Optional[str] = None,
                         source_report: str = "", ingestion_id: str = "") -> str:
    slot_id = hashlib.sha256(
        f"{store_id}|{provider_code or provider_name}|{slot_date}|{slot_time}".encode()
    ).hexdigest()[:24]
    conn.execute(
        """INSERT OR REPLACE INTO provider_schedule_slots
           (slot_id, store_id, provider_name, provider_code, slot_date, slot_time,
            slot_state, is_appt_start, client_name_raw, service_description,
            status_raw, notes_raw, source_report, ingestion_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (slot_id, store_id, provider_name, provider_code, slot_date, slot_time,
         slot_state, is_appt_start, client_name_raw, service_description,
         status_raw, notes_raw, source_report, ingestion_id),
    )
    return slot_id


# ── Client risk snapshot ──────────────────────────────────────────────────────

def insert_risk_snapshot(conn: sqlite3.Connection, store_id: str, client_key: Optional[str],
                         snapshot_date: str, cancel_no_show_count: Optional[int],
                         ingestion_id: str):
    import json as _json

    risk_score = float(cancel_no_show_count) if cancel_no_show_count else 0.0
    if risk_score >= 3:
        risk_band = "high"
    elif risk_score >= 1:
        risk_band = "medium"
    else:
        risk_band = "low"

    snapshot_id = hashlib.sha256(
        f"{store_id}|{client_key}|{snapshot_date}".encode()
    ).hexdigest()[:24]

    conn.execute(
        """INSERT OR REPLACE INTO client_risk_snapshot
           (snapshot_id, store_id, client_key, snapshot_date,
            cancel_no_show_count, risk_score, risk_band, reason_json, ingestion_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (snapshot_id, store_id, client_key, snapshot_date,
         cancel_no_show_count, risk_score, risk_band,
         _json.dumps({"cancel_no_show_count": cancel_no_show_count}),
         ingestion_id),
    )


# ── Forecast channels ─────────────────────────────────────────────────────────

def insert_forecast_channel(conn: sqlite3.Connection, store_id: str,
                            forecast_date: Optional[str], booking_channel: str,
                            booking_count: int, source_report: str,
                            ingestion_id: str):
    forecast_id = hashlib.sha256(
        f"{store_id}|{forecast_date}|{booking_channel}|{ingestion_id}".encode()
    ).hexdigest()[:24]
    conn.execute(
        """INSERT OR REPLACE INTO forecast_daily_channels
           (forecast_id, store_id, forecast_date, booking_channel,
            booking_count, source_report, ingestion_id)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (forecast_id, store_id, forecast_date, booking_channel,
         booking_count, source_report, ingestion_id),
    )


# ── Provider timesheets ───────────────────────────────────────────────────────

def upsert_provider_timesheet(conn: sqlite3.Connection, store_id: str,
                               provider_name_raw: str, provider_name_norm: str,
                               work_date: str, time_in: Optional[str],
                               time_out: Optional[str], hours_worked: Optional[float],
                               is_complete: int, source_report: str,
                               ingestion_id: str) -> str:
    timesheet_id = hashlib.sha256(
        f"{store_id}|{provider_name_norm}|{work_date}".encode()
    ).hexdigest()[:24]
    conn.execute(
        """INSERT OR REPLACE INTO provider_timesheets
           (timesheet_id, store_id, provider_name_raw, provider_name_norm,
            work_date, time_in, time_out, hours_worked, is_complete,
            source_report, ingestion_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (timesheet_id, store_id, provider_name_raw, provider_name_norm,
         work_date, time_in, time_out, hours_worked, is_complete,
         source_report, ingestion_id),
    )
    return timesheet_id
