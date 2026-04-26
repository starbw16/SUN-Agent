"""
Risk Sentinel: flags appointments for the next N days where the client has a
cancel/no-show history above threshold.
"""
import json
from datetime import date, timedelta
from ..persistence.store_silo import get_db, load_store_config


def get_at_risk_appointments(store_id: str, lookahead_days: int = 3, as_of: date = None) -> list:
    """
    Return upcoming appointments (next `lookahead_days`) for clients with
    elevated cancel/no-show history.

    Each result dict:
        appointment_date, start_time, client_name, phone,
        cancel_no_show_count, risk_band, provider_code, service_description
    """
    config = load_store_config(store_id)
    thresholds = config.get("risk_thresholds", {"high": 3, "medium": 1})
    if isinstance(thresholds, str):
        thresholds = json.loads(thresholds)

    high_thresh = int(thresholds.get("high", 3))
    medium_thresh = int(thresholds.get("medium", 1))

    today = as_of or date.today()
    end_date = today + timedelta(days=lookahead_days)

    conn = get_db(store_id)
    try:
        rows = conn.execute(
            """
            SELECT a.appointment_date, a.start_time, a.provider_code,
                   a.service_description, a.client_key,
                   c.client_name_normalized, c.primary_phone,
                   COALESCE(MAX(r.cancel_no_show_count), 0) AS cnc
            FROM appointments a
            JOIN clients c ON c.client_key = a.client_key AND c.store_id = a.store_id
            LEFT JOIN client_risk_snapshot r
                   ON r.store_id = a.store_id
                  AND r.client_key IN (
                      SELECT client_key FROM clients
                      WHERE store_id = a.store_id
                        AND client_name_normalized = c.client_name_normalized
                  )
            WHERE a.store_id = ?
              AND a.appointment_date >= ?
              AND a.appointment_date <= ?
              AND COALESCE(a.status_raw, '') NOT IN ('Cancelled', 'No-Show', 'Deleted')
            GROUP BY a.appointment_key
            HAVING cnc >= ?
            ORDER BY a.appointment_date, a.start_time
            """,
            (store_id, str(today), str(end_date), medium_thresh),
        ).fetchall()
    finally:
        conn.close()

    results = []
    for r in rows:
        cnc = r["cnc"]
        band = "high" if cnc >= high_thresh else "medium"
        results.append({
            "appointment_date": r["appointment_date"],
            "start_time": r["start_time"],
            "provider_code": r["provider_code"],
            "service_description": r["service_description"],
            "client_name": r["client_name_normalized"],
            "phone": r["primary_phone"],
            "cancel_no_show_count": cnc,
            "risk_band": band,
        })

    return results
