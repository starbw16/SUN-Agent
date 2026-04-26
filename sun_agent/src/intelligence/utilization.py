"""
Utilization engine: rolling average utilization per provider and overall,
across all days present in provider_schedule_slots.
Each day's booked/total is computed independently, then averaged across days.
"""
from datetime import date as _date
from ..persistence.store_silo import get_db, load_store_config


def _hours_to_appt_slots(hours: float, slot_duration_minutes: int) -> int:
    """Convert worked hours into available appointment slots."""
    return int(hours * 60 / slot_duration_minutes)


def get_avg_utilization(store_id: str, days_back: int = 7,
                        reference_date=None) -> dict:
    """
    Return rolling average utilization across the past `days_back` days on record.

    Returns:
        {
            "days_on_record": int,
            "days_back": int,
            "avg_utilization": float,
            "avg_pct": int,
            "by_provider": [
                {"provider": str, "avg_utilization": float, "avg_pct": int, "days_on_record": int},
                ...
            ]
        }
    """
    ref = str(reference_date or _date.today())
    conn = get_db(store_id)
    try:
        # Join schedule slots with timesheet hours.
        # When timesheet is available and complete, use actual hours worked as denominator.
        # When not available, fall back to schedule slot count.
        rows = conn.execute(
            """
            SELECT
                s.provider_name,
                s.slot_date,
                COUNT(*) AS total_slots,
                SUM(CASE WHEN s.slot_state = 'booked' THEN 1 ELSE 0 END) AS booked_slots,
                SUM(CASE WHEN s.is_appt_start = 1 THEN 1 ELSE 0 END) AS booked_appts,
                t.hours_worked AS timesheet_hours,
                t.is_complete  AS timesheet_complete
            FROM provider_schedule_slots s
            LEFT JOIN provider_timesheets t
                   ON lower(trim(t.provider_name_norm)) = lower(trim(s.provider_name))
                  AND t.store_id = s.store_id
                  AND t.work_date = s.slot_date
            WHERE s.store_id = ?
              AND s.slot_date IS NOT NULL
              AND s.provider_name IS NOT NULL
              AND s.slot_date BETWEEN date(?, ?) AND date(?)
            GROUP BY s.provider_name, s.slot_date, t.hours_worked, t.is_complete
            """,
            (store_id, ref, f"-{days_back} days", ref),
        ).fetchall()
    finally:
        conn.close()

    config = load_store_config(store_id)
    slot_duration = int(config.get("slot_duration_minutes", 5))
    divisor = max(1, slot_duration // 5)

    if not rows:
        return {
            "days_on_record": 0,
            "days_back": days_back,
            "slot_duration_minutes": slot_duration,
            "avg_utilization": 0.0,
            "avg_pct": 0,
            "by_provider": [],
        }

    # Collect per-day utilization rates and booked counts per provider
    provider_days: dict[str, list[float]] = {}
    provider_booked: dict[str, list[int]] = {}  # provider -> list of daily booked appt counts

    store_day_totals: dict[str, dict] = {}  # date -> {total, booked}

    for r in rows:
        provider = r["provider_name"]
        day = r["slot_date"]
        # booked_appts = exact count of appointment starts (is_appt_start=1)
        booked_appts = r["booked_appts"] or 0
        timesheet_hrs = r["timesheet_hours"]
        timesheet_ok = r["timesheet_complete"]

        # Denominator: use actual hours worked when timesheet is complete,
        # otherwise fall back to schedule slot count converted to appointments.
        if timesheet_hrs and timesheet_ok:
            total_appts = _hours_to_appt_slots(timesheet_hrs, slot_duration)
            data_source = "timesheet"
        else:
            total_appts = r["total_slots"] // divisor
            data_source = "schedule"

        rate = booked_appts / total_appts if total_appts else 0.0

        provider_days.setdefault(provider, []).append(rate)
        provider_booked.setdefault(provider, []).append(booked_appts)

        if day not in store_day_totals:
            store_day_totals[day] = {"total": 0, "booked": 0, "source": data_source}
        store_day_totals[day]["total"] += total_appts
        store_day_totals[day]["booked"] += booked_appts

    days_on_record = len(store_day_totals)

    # Store-level avg: average of each day's store-wide utilization rate
    day_rates = [
        v["booked"] / v["total"]
        for v in store_day_totals.values()
        if v["total"] > 0
    ]
    avg_store = sum(day_rates) / len(day_rates) if day_rates else 0.0

    by_provider = sorted(
        [
            {
                "provider": provider,
                "avg_utilization": round(sum(rates) / len(rates), 2),
                "avg_pct": round(sum(rates) / len(rates) * 100),
                "days_on_record": len(rates),
                "avg_booked": round(sum(provider_booked.get(provider, [0])) / max(len(provider_booked.get(provider, [1])), 1), 1),
            }
            for provider, rates in provider_days.items()
        ],
        key=lambda x: x["avg_utilization"],
        reverse=True,
    )

    avg_total_appts = round(
        sum(v["total"] for v in store_day_totals.values()) / len(store_day_totals) / divisor
    ) if store_day_totals else 0
    avg_booked_appts = round(
        sum(v["booked"] for v in store_day_totals.values()) / len(store_day_totals) / divisor
    ) if store_day_totals else 0

    return {
        "days_on_record": days_on_record,
        "days_back": days_back,
        "slot_duration_minutes": slot_duration,
        "avg_utilization": round(avg_store, 2),
        "avg_pct": round(avg_store * 100),
        "avg_booked_appts": avg_booked_appts,
        "avg_total_appts": avg_total_appts,
        "by_provider": by_provider,
    }


def get_daily_series(store_id: str, days_back: int = 30,
                     reference_date=None) -> dict:
    """
    Return per-day utilization pct for each provider over the past `days_back` days.
    Used to render sparkline graphs.

    Returns:
        {
            "by_provider": {
                "Angela Licari": [{"date": "2026-04-21", "pct": 54}, ...],
                ...
            }
        }
    """
    ref = str(reference_date or _date.today())
    conn = get_db(store_id)
    try:
        rows = conn.execute(
            """
            SELECT
                s.provider_name,
                s.slot_date,
                SUM(s.is_appt_start) AS booked_appts,
                COUNT(*) AS total_slots,
                t.hours_worked,
                t.is_complete
            FROM provider_schedule_slots s
            LEFT JOIN provider_timesheets t
                   ON lower(trim(t.provider_name_norm)) = lower(trim(s.provider_name))
                  AND t.store_id = s.store_id
                  AND t.work_date = s.slot_date
            WHERE s.store_id = ?
              AND s.slot_date IS NOT NULL
              AND s.provider_name IS NOT NULL
              AND s.slot_date BETWEEN date(?, ?) AND date(?)
            GROUP BY s.provider_name, s.slot_date
            ORDER BY s.provider_name, s.slot_date
            """,
            (store_id, ref, f"-{days_back} days", ref),
        ).fetchall()
    finally:
        conn.close()

    config = load_store_config(store_id)
    slot_duration = int(config.get("slot_duration_minutes", 5))
    divisor = max(1, slot_duration // 5)

    by_provider: dict[str, list] = {}
    for r in rows:
        provider = r["provider_name"]
        booked = r["booked_appts"] or 0
        timesheet_hrs = r["hours_worked"]
        timesheet_ok = r["is_complete"]

        if timesheet_hrs and timesheet_ok:
            total = _hours_to_appt_slots(timesheet_hrs, slot_duration)
        else:
            total = r["total_slots"] // divisor

        pct = round(booked / total * 100) if total else 0
        by_provider.setdefault(provider, []).append({
            "date": r["slot_date"],
            "pct": pct,
            "booked": booked,
            "total": total,
        })

    # Store-level daily totals for the aggregate sparkline
    store_daily: list[dict] = []
    day_totals: dict[str, dict] = {}
    for r in rows:
        day = r["slot_date"]
        booked = r["booked_appts"] or 0
        if day not in day_totals:
            day_totals[day] = {"booked": 0}
        day_totals[day]["booked"] += booked
    for day in sorted(day_totals):
        store_daily.append({"date": day, "booked": day_totals[day]["booked"]})

    return {"by_provider": by_provider, "store_daily": store_daily}
