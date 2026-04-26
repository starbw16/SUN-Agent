#!/usr/bin/env python3
"""
Generates static HTML client dashboard pages into public/<store_id>/index.html.
Run locally after ingesting new data, then push to GitHub.
Cloudflare Pages serves the result at <pages_url>/<store_id>/.

Usage:
    python3 generate_pages.py              # regenerate all stores
    python3 generate_pages.py grand_rapids_mi   # one store
"""
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent / "sun_agent"))

from src.reporting.brief_builder import build_brief
from src.persistence.store_silo import STORES_ROOT


# ── SVG helpers ──────────────────────────────────────────────────────────────

def _sparkline(values, width=320, height=52, color="#e63946", show_fill=True):
    """Build an inline SVG polyline sparkline from a list of numeric values."""
    if not values or max(values, default=0) == 0:
        return f'<svg width="{width}" height="{height}"></svg>'
    max_v = max(values)
    pad = 5
    n = len(values)
    pts = []
    for i, v in enumerate(values):
        x = pad + (i * (width - pad * 2) // max(n - 1, 1))
        y = round((height - pad) - (v / max_v * (height - pad * 2)), 1)
        pts.append((x, y))
    line = " ".join(f"{x},{y}" for x, y in pts)
    area = f"{pts[0][0]},{height} {line} {pts[-1][0]},{height}"
    fill = f'<polygon points="{area}" fill="{color}" opacity="0.12"/>' if show_fill else ""
    return (
        f'<svg width="{width}" height="{height}" style="display:block;overflow:visible">'
        f'{fill}'
        f'<polyline points="{line}" fill="none" stroke="{color}" stroke-width="2"/>'
        f'</svg>'
    )


def _mini_sparkline(values, width=120, height=26, color="#e63946"):
    return _sparkline(values, width=width, height=height, color=color, show_fill=False)


# ── Page renderer ─────────────────────────────────────────────────────────────

def render_page_html(brief: dict) -> str:
    store_name  = brief["store_name"]
    brief_date  = brief["brief_date"]
    config      = brief["config"]
    retention   = brief["retention"]
    risk        = brief["risk"]
    booking_src = brief["booking_source"]
    rebook      = brief.get("rebook_time", {})
    u30 = brief.get("utilization_30", {})
    u7  = brief.get("utilization_7",  brief.get("utilization", {}))
    u3  = brief.get("utilization_3",  {})
    ud  = brief.get("utilization_day", {})
    series      = brief.get("utilization_series", {})
    booking_url = config.get("booking_url", "")
    generated   = datetime.now().strftime("%B %d, %Y at %-I:%M %p")

    # ── Utilization headline ──────────────────────────────────────────────────
    pct30 = u30.get("avg_pct") if u30.get("days_on_record") else None
    pct7  = u7.get("avg_pct", 0)
    pct3  = u3.get("avg_pct") if u3.get("days_on_record") else None
    pctd  = ud.get("avg_pct") if ud.get("days_on_record") else None
    diff  = (pct3 - pct7) if pct3 is not None else 0
    tcolor = "#27ae60" if diff >= 5 else ("#e74c3c" if diff <= -5 else "#3498db")
    arrow = "↑" if diff >= 5 else ("↓" if diff <= -5 else "→")
    dpp   = f"+{diff}pp" if diff > 0 else (f"{diff}pp" if diff != 0 else "flat")

    def stat_box(label, value, sub, color="#1a1a2e"):
        return (
            f'<div style="text-align:center;padding:14px 20px;background:#f8f9ff;'
            f'border-radius:8px;border:1px solid #e0e0ee;flex:1;min-width:90px">'
            f'<div style="font-size:11px;color:#aaa;text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px">{label}</div>'
            f'<div style="font-size:28px;font-weight:700;color:{color};line-height:1">{value}</div>'
            f'<div style="font-size:11px;color:#aaa;margin-top:4px">{sub}</div>'
            f'</div>'
        )

    stat_boxes = '<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px">'
    if pct30 is not None:
        stat_boxes += stat_box("30-Day", f"{pct30}%", f"{u30['days_on_record']}d on record", "#888")
    stat_boxes += stat_box("7-Day", f"{pct7}%", f"{u7.get('avg_booked_appts',0)}/{u7.get('avg_total_appts',0)} appts/day")
    if pct3 is not None:
        stat_boxes += stat_box("3-Day", f"{pct3}%", f"{arrow} {dpp} vs 7-day", tcolor)
    if pctd is not None:
        stat_boxes += stat_box(brief_date, f"{pctd}%", f"{ud['avg_booked_appts']}/{ud['avg_total_appts']} appts")
    stat_boxes += '</div>'

    # ── Store-level 30-day booked sparkline ──────────────────────────────────
    store_daily = series.get("store_daily", [])
    store_svg = ""
    if len(store_daily) >= 2:
        booked_vals = [d["booked"] for d in store_daily]
        dates = [d["date"] for d in store_daily]
        store_svg = f"""
<div style="background:#fff8f8;border:1px solid #fce4e4;border-radius:8px;padding:14px 16px;margin-bottom:16px">
  <div style="font-size:12px;color:#888;margin-bottom:8px;font-weight:600;text-transform:uppercase;letter-spacing:.05em">
    Booked Appointments — Last 30 Days
  </div>
  {_sparkline(booked_vals, width=560, height=60)}
  <div style="display:flex;justify-content:space-between;font-size:10px;color:#ccc;margin-top:4px">
    <span>{dates[0]}</span><span>{dates[-1]}</span>
  </div>
</div>"""

    # ── Per-provider table ────────────────────────────────────────────────────
    p7_map  = {p["provider"]: p for p in u7.get("by_provider", [])}
    p30_map = {p["provider"]: p for p in u30.get("by_provider", [])}
    p3_map  = {p["provider"]: p for p in u3.get("by_provider", [])}
    pd_map  = {p["provider"]: p for p in ud.get("by_provider", [])}
    series_map = series.get("by_provider", {})

    provider_rows = ""
    for prov in p7_map:
        r7  = p7_map[prov]
        r30 = p30_map.get(prov)
        r3  = p3_map.get(prov)
        rd  = pd_map.get(prov)
        d   = (r3["avg_pct"] - r7["avg_pct"]) if r3 else 0
        tc  = "#27ae60" if d >= 5 else ("#e74c3c" if d <= -5 else "#888")
        arr = "↑" if d >= 5 else ("↓" if d <= -5 else "→")
        dpp_p = f"+{d}pp" if d > 0 else (f"{d}pp" if d != 0 else "flat")
        v30 = f"{r30['avg_pct']}%" if r30 else "—"
        v3  = f"{r3['avg_pct']}%" if r3 else "—"
        vd  = f"<strong>{rd['avg_pct']}%</strong>" if rd else "<span style='color:#ddd'>—</span>"

        prov_series = series_map.get(prov, [])
        spark = _mini_sparkline([p["pct"] for p in prov_series]) if len(prov_series) >= 2 else ""

        provider_rows += f"""<tr>
          <td style="font-weight:500">{prov}</td>
          <td style="color:#aaa">{v30}</td>
          <td><strong>{r7['avg_pct']}%</strong></td>
          <td>{v3}</td>
          <td style="color:{tc};font-weight:700;white-space:nowrap">{arr} <span style="font-size:11px;font-weight:400">{dpp_p}</span></td>
          <td>{vd}</td>
          <td style="padding:4px 10px">{spark}</td>
        </tr>"""

    util_table = f"""
<table style="border-collapse:collapse;width:100%;margin-bottom:16px">
  <tr>
    <th>Provider</th><th>30-Day</th><th>7-Day</th><th>3-Day</th>
    <th>Trend</th><th>{brief_date}</th><th>30-Day Trend</th>
  </tr>
  {provider_rows if provider_rows else '<tr><td colspan="7" style="color:#aaa">No schedule data yet.</td></tr>'}
</table>"""

    # ── Retention ─────────────────────────────────────────────────────────────
    retention_rows = ""
    for w in retention["windows"]:
        cnt = len(retention["buckets"].get(w, []))
        retention_rows += f"<tr><td>{w // 7} weeks</td><td>{cnt}</td></tr>"
    book_link = f'<p><a href="{booking_url}" style="color:#e63946">Book online →</a></p>' if booking_url else ""

    # ── Risk ──────────────────────────────────────────────────────────────────
    risk_rows = ""
    if risk:
        for r in risk:
            color = "#c0392b" if r["risk_band"] == "high" else "#e67e22"
            risk_rows += (
                f'<tr><td>{r["appointment_date"]}</td><td>{r["start_time"] or ""}</td>'
                f'<td>{r["client_name"]}</td>'
                f'<td style="color:{color}">{r["cancel_no_show_count"]}x [{r["risk_band"].upper()}]</td></tr>'
            )
    else:
        risk_rows = '<tr><td colspan="4" style="color:#aaa">None flagged</td></tr>'

    # ── Rebook ────────────────────────────────────────────────────────────────
    rebook_rows = ""
    if rebook.get("by_category"):
        for cat in rebook["by_category"]:
            rebook_rows += f"<tr><td>{cat['category']}</td><td>{cat['avg_weeks']} wks</td><td>{cat['sample_size']}</td></tr>"

    # ── Booking channels ──────────────────────────────────────────────────────
    channel_rows = ""
    for ch in booking_src["channels"]:
        channel_rows += f"<tr><td>{ch['channel']}</td><td>{ch['count']}</td><td>{ch['pct']}%</td></tr>"

    def section(title):
        return f'<h2 style="color:#1a1a2e;border-bottom:2px solid #e63946;padding-bottom:6px;margin-top:32px">{title}</h2>'

    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{store_name} — SUN-Agent Dashboard</title>
<style>
  body {{ font-family:Arial,sans-serif; color:#333; max-width:760px; margin:auto; padding:24px 20px; }}
  h1 {{ color:#1a1a2e; margin-bottom:2px; }}
  h2 {{ color:#1a1a2e; border-bottom:2px solid #e63946; padding-bottom:6px; margin-top:32px; }}
  table {{ border-collapse:collapse; width:100%; margin-bottom:16px; }}
  th {{ background:#1a1a2e; color:#fff; padding:7px 10px; text-align:left; font-size:12px; }}
  td {{ padding:6px 10px; border-bottom:1px solid #eee; font-size:13px; }}
  .badge {{ display:inline-block; padding:2px 8px; border-radius:4px; font-size:11px; font-weight:bold; color:#fff; }}
  .updated {{ font-size:11px; color:#bbb; margin-bottom:24px; }}
</style>
</head><body>

<h1>{store_name}</h1>
<p style="color:#888;margin-top:0">Report date: <strong>{brief_date}</strong></p>
<p class="updated">Last updated: {generated}</p>

{section("Chair Utilization")}
{stat_boxes}
{store_svg}
{util_table}

{section(f"Lapsed Clients — {retention['total_lapsed']} total")}
<table><tr><th>Window</th><th>Clients</th></tr>{retention_rows}</table>
{book_link}

{section(f"Risk Appointments — next 3 days ({len(risk)} flagged)")}
<table><tr><th>Date</th><th>Time</th><th>Client</th><th>Risk</th></tr>{risk_rows}</table>

{section(f"Avg Rebook Time — {rebook.get('avg_weeks', '—')} weeks overall")}
{"<table><tr><th>Service Category</th><th>Avg Return</th><th>Sample</th></tr>" + rebook_rows + "</table>" if rebook_rows else "<p style='color:#aaa'>Not enough data yet.</p>"}

{section("Booking Channels — last 7 days")}
{"<table><tr><th>Channel</th><th>Bookings</th><th>Share</th></tr>" + channel_rows + "</table>" if channel_rows else "<p style='color:#aaa'>No channel data yet.</p>"}

<hr style="margin-top:40px;border:none;border-top:1px solid #eee">
<p style="font-size:11px;color:#aaa">SUN-Agent | Powered by Salon Ultimate data</p>
</body></html>"""


# ── Landing index page ────────────────────────────────────────────────────────

def render_index_html(stores: list[dict]) -> str:
    rows = ""
    for s in stores:
        rows += (
            f'<li style="margin:12px 0">'
            f'<a href="/{s["store_id"]}/" style="font-size:16px;color:#e63946;font-weight:600">{s["store_name"]}</a>'
            f'<span style="color:#aaa;font-size:12px;margin-left:12px">{s["store_id"]}</span>'
            f'</li>'
        )
    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><title>SUN-Agent</title>
<style>body{{font-family:Arial,sans-serif;max-width:560px;margin:60px auto;padding:0 20px}}</style>
</head><body>
<h1 style="color:#1a1a2e">SUN-Agent</h1>
<p style="color:#888">Salon intelligence dashboards</p>
<ul style="list-style:none;padding:0">{rows}</ul>
<hr style="margin-top:40px;border:none;border-top:1px solid #eee">
<p style="font-size:11px;color:#aaa">SUN-Agent | Powered by Salon Ultimate data</p>
</body></html>"""


# ── Main ──────────────────────────────────────────────────────────────────────

def list_store_ids() -> list[str]:
    if not STORES_ROOT.exists():
        return []
    return [p.name for p in STORES_ROOT.iterdir() if (p / "sun_agent.db").exists()]


def main():
    target_ids = sys.argv[1:] or list_store_ids()
    if not target_ids:
        print("No stores found. Ingest data first.")
        sys.exit(1)

    public = Path(__file__).parent / "public"
    store_meta = []

    for store_id in target_ids:
        print(f"Building {store_id}...", end=" ", flush=True)
        try:
            brief = build_brief(store_id)
            html  = render_page_html(brief)
            out   = public / store_id / "index.html"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(html, encoding="utf-8")
            store_meta.append({"store_id": store_id, "store_name": brief["store_name"]})
            print("✓")
        except Exception as e:
            print(f"ERROR: {e}")

    # Landing index
    index_html = render_index_html(store_meta)
    (public / "index.html").write_text(index_html, encoding="utf-8")
    print(f"\nDone. Pages written to public/")
    print("Push to GitHub to deploy via Cloudflare Pages.")


if __name__ == "__main__":
    main()
