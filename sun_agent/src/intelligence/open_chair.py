"""
Open Chair Engine: derives booked coverage from provider_schedule_slots for today/tomorrow.
Produces per-provider and overall summary with utilization tier classification.
"""
from datetime import date, timedelta
from ..persistence.store_silo import get_db, load_store_config

TIER_THRESHOLDS = {
    "low":    (0.0,  0.40),
    "mid":    (0.40, 0.70),
    "growth": (0.70, 1.01),
}

DAYPART_HOURS = {
    "morning":   range(8, 12),
    "afternoon": range(12, 17),
    "evening":   range(17, 21),
}


def _classify_tier(coverage: float) -> str:
    for tier, (lo, hi) in TIER_THRESHOLDS.items():
        if lo <= coverage < hi:
            return tier
    return "growth"


def _daypart(time_str: str) -> str:
    try:
        hour = int(time_str.split(":")[0])
    except (ValueError, AttributeError):
        return "unknown"
    for name, hrs in DAYPART_HOURS.items():
        if hour in hrs:
            return name
    return "other"


def _appt_divisor(config: dict) -> int:
    """How many raw 5-min grid slots equal one appointment slot."""
    duration = int(config.get("slot_duration_minutes", 5))
    return max(1, duration // 5)


def get_open_chair_summary(store_id: str, target_date: date = None) -> dict:
    """
    Summarize open vs booked slots for target_date (defaults to today).

    Returns:
        {
            "date": str,
            "total_slots": int,
            "booked_slots": int,
            "open_slots": int,
            "coverage": float,          # 0.0–1.0
            "tier": str,                # low / mid / growth
            "open_by_daypart": {"morning": int, "afternoon": int, "evening": int},
            "by_provider": [{"provider": str, "total": int, "booked": int, "open": int, "coverage": float}, ...],
        }
    """
    target_date = target_date or date.today()
    config = load_store_config(store_id)

    conn = get_db(store_id)
    try:
        rows = conn.execute(
            """
            SELECT provider_name, provider_code, slot_time, slot_state
            FROM provider_schedule_slots
            WHERE store_id = ? AND slot_date = ?
            """,
            (store_id, str(target_date)),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {
            "date": str(target_date),
            "total_slots": 0, "booked_slots": 0, "open_slots": 0,
            "coverage": 0.0, "tier": config.get("utilization_tier", "mid"),
            "open_by_daypart": {"morning": 0, "afternoon": 0, "evening": 0},
            "by_provider": [],
        }

    provider_map = {}
    open_by_daypart = {"morning": 0, "afternoon": 0, "evening": 0}

    for r in rows:
        pkey = r["provider_name"] or r["provider_code"] or "Unknown"
        if pkey not in provider_map:
            provider_map[pkey] = {"total": 0, "booked": 0, "open": 0}
        provider_map[pkey]["total"] += 1
        if r["slot_state"] == "booked":
            provider_map[pkey]["booked"] += 1
        elif r["slot_state"] == "open":
            provider_map[pkey]["open"] += 1
            dp = _daypart(r["slot_time"])
            if dp in open_by_daypart:
                open_by_daypart[dp] += 1

    divisor = _appt_divisor(config)

    total = sum(p["total"] for p in provider_map.values()) // divisor
    booked = sum(p["booked"] for p in provider_map.values()) // divisor
    open_count = sum(p["open"] for p in provider_map.values()) // divisor
    coverage = booked / total if total else 0.0

    by_provider = []
    for name, p in sorted(provider_map.items()):
        p_total = p["total"] // divisor
        p_booked = p["booked"] // divisor
        p_open = p["open"] // divisor
        p_cov = p_booked / p_total if p_total else 0.0
        by_provider.append({
            "provider": name,
            "total": p_total,
            "booked": p_booked,
            "open": p_open,
            "coverage": round(p_cov, 2),
        })

    return {
        "date": str(target_date),
        "total_slots": total,
        "booked_slots": booked,
        "open_slots": open_count,
        "coverage": round(coverage, 2),
        "tier": _classify_tier(coverage),
        "slot_duration_minutes": config.get("slot_duration_minutes", 5),
        "open_by_daypart": open_by_daypart,
        "by_provider": by_provider,
    }
