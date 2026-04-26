"""
Assembles all intelligence signals into a single brief payload dict.
"""
from datetime import date
from ..intelligence.retention import get_lapsed_clients
from ..intelligence.risk_sentinel import get_at_risk_appointments
from ..intelligence.open_chair import get_open_chair_summary
from ..intelligence.booking_source import get_booking_source_summary
from ..intelligence.rebook_time import get_rebook_time
from ..intelligence.utilization import get_avg_utilization, get_daily_series
from ..persistence.store_silo import load_store_config


def build_brief(store_id: str, brief_date: date = None) -> dict:
    """
    Gather all signals for one store and return a structured brief payload.

    Returns:
        {
            "store_id": str,
            "store_name": str,
            "brief_date": str,
            "config": dict,
            "retention": {...},
            "risk": [...],
            "open_chair": {...},
            "booking_source": {...},
        }
    """
    brief_date = brief_date or date.today()
    config = load_store_config(store_id)

    retention = get_lapsed_clients(store_id, as_of=brief_date)
    risk = get_at_risk_appointments(store_id, lookahead_days=3, as_of=brief_date)
    open_chair = get_open_chair_summary(store_id, target_date=brief_date)
    booking_source = get_booking_source_summary(store_id, days_back=7, reference_date=brief_date)
    rebook_time = get_rebook_time(store_id)
    utilization_30 = get_avg_utilization(store_id, days_back=30, reference_date=brief_date)
    utilization_7  = get_avg_utilization(store_id, days_back=7,  reference_date=brief_date)
    utilization_3  = get_avg_utilization(store_id, days_back=3,  reference_date=brief_date)
    utilization_day = get_avg_utilization(store_id, days_back=0, reference_date=brief_date)
    utilization_series = get_daily_series(store_id, days_back=30, reference_date=brief_date)

    return {
        "store_id": store_id,
        "store_name": config.get("store_name", store_id),
        "brief_date": str(brief_date),
        "config": config,
        "retention": retention,
        "risk": risk,
        "open_chair": open_chair,
        "booking_source": booking_source,
        "rebook_time": rebook_time,
        "utilization": utilization_7,
        "utilization_30": utilization_30,
        "utilization_7": utilization_7,
        "utilization_3": utilization_3,
        "utilization_day": utilization_day,
        "utilization_series": utilization_series,
    }
