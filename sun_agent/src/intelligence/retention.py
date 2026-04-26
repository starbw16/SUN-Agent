"""
Retention engine: identifies clients who haven't visited within configurable windows.
Returns lapsed client lists bucketed by days-since-last-visit.
"""
import json
from datetime import date, timedelta
from ..persistence.store_silo import get_db, load_store_config


def get_lapsed_clients(store_id: str, as_of: date = None) -> dict:
    """
    Query lapsed clients by retention window.

    Returns:
        {
            "windows": [28, 42, 56],
            "buckets": {
                28: [{"client_key": ..., "name": ..., "phone": ..., "last_visit": ..., "days_lapsed": ..., "last_service": ...}, ...],
                42: [...],
                56: [...],
            },
            "total_lapsed": int,
        }
    """
    as_of = as_of or date.today()
    config = load_store_config(store_id)
    windows = config.get("retention_windows_days", [28, 42, 56])
    if isinstance(windows, str):
        windows = json.loads(windows)
    windows = sorted(windows)

    conn = get_db(store_id)
    try:
        rows = conn.execute(
            """
            SELECT c.client_key, c.client_name_normalized, c.primary_phone,
                   c.last_seen_date, c.last_service_description
            FROM clients c
            WHERE c.last_seen_date IS NOT NULL
              AND c.primary_phone IS NOT NULL
              AND c.primary_phone != ''
            """,
        ).fetchall()
    finally:
        conn.close()

    buckets = {w: [] for w in windows}
    max_window = max(windows)

    for row in rows:
        try:
            last_visit = date.fromisoformat(row["last_seen_date"])
        except (ValueError, TypeError):
            continue
        days_lapsed = (as_of - last_visit).days
        if days_lapsed <= 0 or days_lapsed > max_window + 30:
            continue

        client = {
            "client_key": row["client_key"],
            "name": row["client_name_normalized"],
            "phone": row["primary_phone"],
            "last_visit": str(last_visit),
            "days_lapsed": days_lapsed,
            "last_service": row["last_service_description"] or "",
        }

        assigned = False
        for w in windows:
            lower = windows[windows.index(w) - 1] if windows.index(w) > 0 else 0
            if lower < days_lapsed <= w:
                buckets[w].append(client)
                assigned = True
                break

    total = sum(len(v) for v in buckets.values())
    return {"windows": windows, "buckets": buckets, "total_lapsed": total}
