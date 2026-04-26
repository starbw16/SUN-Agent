"""
Booking Source Engine: analyzes channel mix from forecast_daily_channels.
"""
from datetime import date, timedelta
from ..persistence.store_silo import get_db


def get_booking_source_summary(store_id: str, days_back: int = 7,
                               reference_date: date = None) -> dict:
    """
    Summarize booking channel mix for the past `days_back` days ending on `reference_date`.

    Returns:
        {
            "period_start": str,
            "period_end": str,
            "total_bookings": int,
            "channels": [{"channel": str, "count": int, "pct": float}, ...],  # sorted desc
        }
    """
    end_date = reference_date or date.today()
    start_date = end_date - timedelta(days=days_back)

    conn = get_db(store_id)
    try:
        rows = conn.execute(
            """
            SELECT booking_channel, SUM(booking_count) AS total
            FROM forecast_daily_channels
            WHERE store_id = ?
              AND forecast_date >= ?
              AND forecast_date <= ?
            GROUP BY booking_channel
            ORDER BY total DESC
            """,
            (store_id, str(start_date), str(end_date)),
        ).fetchall()
    finally:
        conn.close()

    total = sum(r["total"] for r in rows)
    channels = []
    for r in rows:
        pct = round(r["total"] / total * 100, 1) if total else 0.0
        channels.append({"channel": r["booking_channel"], "count": r["total"], "pct": pct})

    return {
        "period_start": str(start_date),
        "period_end": str(end_date),
        "total_bookings": total,
        "channels": channels,
    }
