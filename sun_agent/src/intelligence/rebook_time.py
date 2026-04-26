"""
Rebook Time: calculates average days between consecutive visits per client,
overall and broken down by service category.
"""
from ..persistence.store_silo import get_db


def get_rebook_time(store_id: str) -> dict:
    """
    Return average rebook time in weeks, overall and by service category.

    Returns:
        {
            "avg_weeks": float,          # overall average across all clients
            "sample_size": int,          # number of gaps used
            "by_category": [
                {"category": str, "avg_weeks": float, "sample_size": int},
                ...
            ]
        }
    """
    conn = get_db(store_id)
    try:
        rows = conn.execute(
            """
            SELECT client_key, visit_date, service_category
            FROM client_visits
            WHERE store_id = ?
              AND visit_date IS NOT NULL
            ORDER BY client_key, visit_date
            """,
            (store_id,),
        ).fetchall()
    finally:
        conn.close()

    # Group visits per client, then compute consecutive gaps
    from datetime import date as date_cls

    visits_by_client: dict[str, list] = {}
    for r in rows:
        visits_by_client.setdefault(r["client_key"], []).append(
            (r["visit_date"], r["service_category"] or "Other")
        )

    all_gaps: list[int] = []
    gaps_by_category: dict[str, list[int]] = {}

    for visits in visits_by_client.values():
        if len(visits) < 2:
            continue
        for i in range(1, len(visits)):
            try:
                d1 = date_cls.fromisoformat(visits[i - 1][0])
                d2 = date_cls.fromisoformat(visits[i][0])
            except (ValueError, TypeError):
                continue
            gap = (d2 - d1).days
            if gap <= 0 or gap > 365:
                continue
            all_gaps.append(gap)
            cat = visits[i][1]
            gaps_by_category.setdefault(cat, []).append(gap)

    def _avg_weeks(gaps: list[int]) -> float:
        if not gaps:
            return 0.0
        return round(sum(gaps) / len(gaps) / 7, 1)

    by_category = sorted(
        [
            {
                "category": cat,
                "avg_weeks": _avg_weeks(gaps),
                "sample_size": len(gaps),
            }
            for cat, gaps in gaps_by_category.items()
            if len(gaps) >= 5
        ],
        key=lambda x: x["avg_weeks"],
    )

    return {
        "avg_weeks": _avg_weeks(all_gaps),
        "sample_size": len(all_gaps),
        "by_category": by_category,
    }
