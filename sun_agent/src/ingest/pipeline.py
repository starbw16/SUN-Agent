"""
Main ingest pipeline. Orchestrates: classify → parse → validate → persist → quarantine.
Entry point: ingest_file(filepath, store_id=None)
"""
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from ..parsers.classifier import get_parser, is_phase1
from ..persistence.store_silo import (
    create_store_silo, get_db, resolve_store_id, load_store_config
)
from ..persistence.repository import (
    log_ingestion_start, log_ingestion_complete, log_ingestion_error,
    upsert_client, insert_visit, upsert_appointment,
    insert_appointment_event, insert_schedule_slot,
    insert_risk_snapshot, insert_forecast_channel,
    upsert_provider_timesheet,
)
from ..normalization.normalizer import make_client_key, normalize_name

logger = logging.getLogger(__name__)

INBOUND_DIR = Path(__file__).resolve().parents[3] / "inbound"
PROCESSED_DIR = Path(__file__).resolve().parents[3] / "processed"
QUARANTINE_DIR = Path(__file__).resolve().parents[3] / "quarantine"


def ingest_file(filepath, store_id: str = None, store_name: str = None) -> dict:
    """
    Ingest a single file.  Returns a status dict with keys: status, store_id,
    report_type, rows_loaded, error.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        return {"status": "error", "error": f"File not found: {filepath}"}

    parser = get_parser(filepath)
    result = parser.parse()

    if not result.ok:
        _quarantine(filepath, result.error)
        return {"status": "quarantined", "error": result.error,
                "report_type": result.report_type}

    # Determine store identity
    resolved_store_id = store_id
    resolved_store_name = store_name
    if not resolved_store_id and result.store_name_raw:
        resolved_store_id = resolve_store_id(result.store_name_raw)
        resolved_store_name = resolved_store_name or result.store_name_raw
    if not resolved_store_id:
        _quarantine(filepath, "Cannot determine store_id from filename or file contents")
        return {"status": "quarantined", "error": "Unknown store"}

    resolved_store_name = resolved_store_name or resolved_store_id

    create_store_silo(resolved_store_id, resolved_store_name)
    conn = get_db(resolved_store_id)

    try:
        ingestion_id = log_ingestion_start(
            conn, resolved_store_id, filepath.name,
            result.report_type, result.file_hash,
            result.report_start_date, result.generated_at,
        )
        if ingestion_id is None:
            conn.close()
            logger.info("Duplicate file skipped: %s (store=%s)", filepath.name, resolved_store_id)
            return {"status": "duplicate", "store_id": resolved_store_id,
                    "report_type": result.report_type, "rows_loaded": 0}

        rows_loaded = _persist_rows(conn, resolved_store_id, result, ingestion_id)

        log_ingestion_complete(conn, ingestion_id, result.row_count_raw, rows_loaded)
        conn.commit()

        _archive(filepath)
        logger.info("Ingested %s → store=%s rows=%d", filepath.name, resolved_store_id, rows_loaded)
        return {
            "status": "ok", "store_id": resolved_store_id,
            "report_type": result.report_type, "rows_loaded": rows_loaded,
        }

    except Exception as exc:
        if ingestion_id:
            log_ingestion_error(conn, ingestion_id, str(exc))
        conn.rollback()
        _quarantine(filepath, str(exc))
        logger.exception("Ingestion failed: %s", filepath)
        return {"status": "error", "error": str(exc)}
    finally:
        conn.close()


def _persist_rows(conn, store_id, result, ingestion_id) -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    loaded = 0

    for row in result.rows:
        rt = row.get("_record_type")

        if rt == "client_visit":
            phone = row.get("primary_phone")
            name_raw = row.get("client_name_raw", "")
            name_norm = row.get("client_name_normalized", normalize_name(name_raw))
            client_key = upsert_client(
                conn, store_id, name_raw, name_norm, phone,
                visit_date=row.get("visit_date"),
                service_description=row.get("service_description"),
            )
            insert_visit(
                conn, store_id, client_key,
                row.get("visit_date"), row.get("service_category"),
                row.get("service_description"), result.report_type, ingestion_id,
            )
            loaded += 1

        elif rt == "appointment":
            phone = row.get("primary_phone")
            name_raw = row.get("client_name_raw", "")
            name_norm = row.get("client_name_normalized", normalize_name(name_raw))
            client_key = upsert_client(conn, store_id, name_raw, name_norm, phone,
                                       preferred_contact=row.get("preferred_contact"))
            upsert_appointment(
                conn, store_id, client_key,
                provider_code=row.get("stylist_code"),
                appointment_date=row.get("appointment_date"),
                start_time=row.get("start_time"),
                end_time=row.get("end_time"),
                service_description=row.get("service_description"),
                status_raw=row.get("status_raw"),
                booking_source=row.get("booking_source"),
                preferred_contact=row.get("preferred_contact"),
                source_report=result.report_type,
                ingestion_id=ingestion_id,
            )
            loaded += 1

        elif rt == "appointment_event":
            name_raw = row.get("client_name_raw", "")
            name_norm = row.get("client_name_normalized", normalize_name(name_raw))
            client_key = upsert_client(conn, store_id, name_raw, name_norm, None)
            appt_key = None
            insert_appointment_event(
                conn, store_id, appt_key,
                event_type=row.get("event_type", "unknown"),
                timestamp_raw=row.get("app_date"),
                actor_code=row.get("who_booked"),
                comments=row.get("comments"),
                remarks=row.get("appt_remarks"),
                source_report=result.report_type,
                ingestion_id=ingestion_id,
            )
            loaded += 1

        elif rt == "provider_schedule_slot":
            insert_schedule_slot(
                conn, store_id,
                provider_name=row.get("provider_name"),
                provider_code=row.get("provider_code"),
                slot_date=row.get("slot_date"),
                slot_time=row.get("slot_time"),
                slot_state=row.get("slot_state", "unknown"),
                is_appt_start=row.get("is_appt_start", 0),
                client_name_raw=row.get("client_name_raw"),
                service_description=row.get("service_description"),
                status_raw=row.get("status_raw"),
                notes_raw=row.get("notes_raw"),
                source_report=result.report_type,
                ingestion_id=ingestion_id,
            )
            loaded += 1

        elif rt == "client_risk":
            name_raw = row.get("client_name_raw", "")
            name_norm = row.get("client_name_normalized", normalize_name(name_raw))
            client_key = upsert_client(conn, store_id, name_raw, name_norm, None)
            insert_risk_snapshot(
                conn, store_id, client_key,
                snapshot_date=today,
                cancel_no_show_count=row.get("cancel_no_show_count"),
                ingestion_id=ingestion_id,
            )
            loaded += 1

        elif rt == "forecast_daily_channel":
            insert_forecast_channel(
                conn, store_id,
                forecast_date=row.get("forecast_date"),
                booking_channel=row.get("booking_channel"),
                booking_count=row.get("booking_count", 0),
                source_report=result.report_type,
                ingestion_id=ingestion_id,
            )
            loaded += 1

        elif rt == "provider_timesheet":
            upsert_provider_timesheet(
                conn, store_id,
                provider_name_raw=row.get("provider_name_raw", ""),
                provider_name_norm=row.get("provider_name_normalized", ""),
                work_date=row.get("work_date"),
                time_in=row.get("time_in"),
                time_out=row.get("time_out"),
                hours_worked=row.get("hours_worked"),
                is_complete=row.get("is_complete", 1),
                source_report=result.report_type,
                ingestion_id=ingestion_id,
            )
            loaded += 1

    return loaded


def _archive(filepath: Path):
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    dest = PROCESSED_DIR / filepath.name
    if not dest.exists():
        shutil.copy2(filepath, dest)


def _quarantine(filepath: Path, reason: str):
    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
    dest = QUARANTINE_DIR / filepath.name
    if not dest.exists():
        shutil.copy2(filepath, dest)
    reason_file = QUARANTINE_DIR / f"{filepath.stem}_reason.txt"
    reason_file.write_text(reason)
    logger.warning("Quarantined %s: %s", filepath.name, reason)


def scan_inbound(store_id: str = None, store_name: str = None) -> list:
    """Process all files currently in the inbound/ directory."""
    results = []
    for fp in sorted(INBOUND_DIR.glob("*")):
        if fp.suffix.lower() in (".xls", ".xlsx", ".csv"):
            results.append(ingest_file(fp, store_id=store_id, store_name=store_name))
    return results
