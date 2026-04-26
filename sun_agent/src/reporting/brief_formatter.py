"""
Converts a brief payload dict into a plain-text and HTML email body.
"""


def format_brief_text(brief: dict) -> str:
    store_name = brief["store_name"]
    brief_date = brief["brief_date"]
    config = brief["config"]
    retention = brief["retention"]
    risk = brief["risk"]
    open_chair = brief["open_chair"]
    booking_src = brief["booking_source"]
    rebook = brief.get("rebook_time", {})
    u30 = brief.get("utilization_30", {})
    u7  = brief.get("utilization_7",  brief.get("utilization", {}))
    u3  = brief.get("utilization_3",  {})
    ud  = brief.get("utilization_day", {})
    booking_url = config.get("booking_url", "")
    pages_url   = config.get("pages_url", "")
    tier = open_chair.get("tier", "mid").upper()

    lines = [
        f"MORNING BRIEF — {store_name}",
        f"Date: {brief_date}",
        "=" * 60,
        "",
    ]

    # Chair Utilization — headline windows
    lines.append("CHAIR UTILIZATION")
    if u7.get("days_on_record"):
        pct30 = u30.get("avg_pct", "—") if u30.get("days_on_record") else "—"
        pct7  = u7.get("avg_pct", 0)
        pct3  = u3.get("avg_pct", "—") if u3.get("days_on_record") else "—"
        diff  = (u3.get("avg_pct", pct7) - pct7) if u3.get("days_on_record") else 0
        arrow = "↑" if diff >= 5 else ("↓" if diff <= -5 else "→")
        lines.append(f"  30-day: {pct30}%   7-day: {pct7}%   3-day: {pct3}%  {arrow}")
        if ud.get("days_on_record"):
            lines.append(f"  {brief_date}: {ud['avg_pct']}%  ({ud['avg_booked_appts']}/{ud['avg_total_appts']} appts)")
        lines.append("")

        # Per-provider trend table
        p30_map = {p["provider"]: p for p in u30.get("by_provider", [])}
        p7_map  = {p["provider"]: p for p in u7.get("by_provider", [])}
        p3_map  = {p["provider"]: p for p in u3.get("by_provider", [])}
        pd_map  = {p["provider"]: p for p in ud.get("by_provider", [])}

        lines.append(f"  {'Provider':<22}  {'30d':>5}  {'7d':>5}  {'3d':>5}  {'Trend':>6}  {brief_date}")
        lines.append("  " + "-" * 62)
        for prov in p7_map:
            r30 = p30_map.get(prov)
            r7  = p7_map.get(prov)
            r3  = p3_map.get(prov)
            rd  = pd_map.get(prov)
            v30 = f"{r30['avg_pct']}%" if r30 else "—"
            v7  = f"{r7['avg_pct']}%" if r7 else "—"
            v3  = f"{r3['avg_pct']}%" if r3 else "—"
            vd  = f"{rd['avg_pct']}%" if rd else "—"
            d   = (r3["avg_pct"] - r7["avg_pct"]) if r3 and r7 else 0
            arr = "↑" if d >= 5 else ("↓" if d <= -5 else "→")
            dpp = f"+{d}pp" if d > 0 else (f"{d}pp" if d != 0 else "flat")
            lines.append(f"  {prov:<22}  {v30:>5}  {v7:>5}  {v3:>5}  {arr} {dpp:<6}  {vd}")
    else:
        cov_pct = round(open_chair["coverage"] * 100)
        lines.append(f"  Today: {open_chair['booked_slots']}/{open_chair['total_slots']} slots ({cov_pct}%)  [{tier}]")
    lines.append("")

    # Retention
    lines.append(f"LAPSED CLIENTS  ({retention['total_lapsed']} total)")
    for w in retention["windows"]:
        bucket = retention["buckets"].get(w, [])
        lines.append(f"  {w // 7}-week window: {len(bucket)} client(s)")
    if booking_url:
        lines.append(f"  Booking link: {booking_url}")
    lines.append("")

    # Risk
    lines.append(f"RISK APPOINTMENTS — next 3 days  ({len(risk)} flagged)")
    if risk:
        for r in risk:
            lines.append(
                f"  {r['appointment_date']} {r['start_time'] or ''}  {r['client_name']:<22}"
                f"  {r['cancel_no_show_count']}x  [{r['risk_band'].upper()}]"
            )
    else:
        lines.append("  None flagged.")
    lines.append("")

    # Rebook Time
    if rebook.get("avg_weeks"):
        lines += [
            f"AVG REBOOK TIME  ({rebook['sample_size']} visit gaps)",
            f"  Overall: {rebook['avg_weeks']} weeks",
        ]
        for cat in rebook.get("by_category", []):
            lines.append(f"  {cat['category']:<25} {cat['avg_weeks']} wks  (n={cat['sample_size']})")
        lines.append("")

    # Booking Source
    lines.append(f"BOOKING CHANNELS — last 7 days  ({booking_src['total_bookings']} bookings)")
    for ch in booking_src["channels"]:
        lines.append(f"  {ch['channel']:<25} {ch['count']:>4}  ({ch['pct']}%)")
    lines.append("")

    if pages_url:
        lines += ["", f"VIEW YOUR DASHBOARD: {pages_url}", ""]
    lines += ["=" * 60, "SUN-Agent | Powered by Salon Ultimate data", ""]
    return "\n".join(lines)


def format_brief_html(brief: dict) -> str:
    store_name = brief["store_name"]
    brief_date = brief["brief_date"]
    config = brief["config"]
    retention = brief["retention"]
    risk = brief["risk"]
    open_chair = brief["open_chair"]
    booking_src = brief["booking_source"]
    rebook = brief.get("rebook_time", {})
    u30 = brief.get("utilization_30", {})
    u7  = brief.get("utilization_7",  brief.get("utilization", {}))
    u3  = brief.get("utilization_3",  {})
    ud  = brief.get("utilization_day", {})
    booking_url = config.get("booking_url", "")
    pages_url   = config.get("pages_url", "")
    tier = open_chair.get("tier", "mid").upper()
    cov_pct = round(open_chair["coverage"] * 100)

    def section(title):
        return f'<h2 style="color:#1a1a2e;border-bottom:2px solid #e63946;padding-bottom:4px;margin-top:28px">{title}</h2>'

    # ── Utilization headline boxes ──────────────────────────────────────────
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
            f'<td style="text-align:center;padding:10px 16px;background:#f8f9ff;'
            f'border-radius:6px;border:1px solid #e0e0ee">'
            f'<div style="font-size:10px;color:#aaa;text-transform:uppercase;letter-spacing:.05em">{label}</div>'
            f'<div style="font-size:22px;font-weight:700;color:{color}">{value}</div>'
            f'<div style="font-size:10px;color:#aaa">{sub}</div>'
            f'</td>'
        )

    util_boxes = '<table style="border-collapse:separate;border-spacing:8px;margin-bottom:4px"><tr>'
    if pct30 is not None:
        util_boxes += stat_box("30-Day", f"{pct30}%", f"{u30['days_on_record']}d on record", "#888")
    util_boxes += stat_box("7-Day", f"{pct7}%", f"{u7.get('avg_booked_appts',0)}/{u7.get('avg_total_appts',0)} appts/day")
    if pct3 is not None:
        util_boxes += stat_box("3-Day", f"{pct3}%", f"{arrow} {dpp} vs 7-day", tcolor)
    if pctd is not None:
        util_boxes += stat_box(brief_date, f"{pctd}%",
                               f"{ud['avg_booked_appts']}/{ud['avg_total_appts']} appts")
    util_boxes += '</tr></table>'

    # ── Per-provider trend table ────────────────────────────────────────────
    p30_map = {p["provider"]: p for p in u30.get("by_provider", [])}
    p7_map  = {p["provider"]: p for p in u7.get("by_provider", [])}
    p3_map  = {p["provider"]: p for p in u3.get("by_provider", [])}
    pd_map  = {p["provider"]: p for p in ud.get("by_provider", [])}

    util_rows = ""
    for prov in p7_map:
        r30 = p30_map.get(prov)
        r7  = p7_map[prov]
        r3  = p3_map.get(prov)
        rd  = pd_map.get(prov)
        d   = (r3["avg_pct"] - r7["avg_pct"]) if r3 else 0
        tc  = "#27ae60" if d >= 5 else ("#e74c3c" if d <= -5 else "#888")
        arr = "↑" if d >= 5 else ("↓" if d <= -5 else "→")
        dpp_p = f"+{d}pp" if d > 0 else (f"{d}pp" if d != 0 else "flat")
        util_rows += (
            f"<tr>"
            f"<td>{prov}</td>"
            f"<td style='color:#aaa'>{r30['avg_pct']}%" if r30 else "<td style='color:#ddd'>—"
            f"</td>"
            f"<td><strong>{r7['avg_pct']}%</strong></td>"
            f"<td>{r3['avg_pct']}%" if r3 else "<td style='color:#ddd'>—"
            f"</td>"
            f"<td style='color:{tc};font-weight:700'>{arr} <span style='font-size:11px;font-weight:400'>{dpp_p}</span></td>"
            f"<td><strong>{rd['avg_pct']}%</strong></td>" if rd else "<td style='color:#ddd'>—</td>"
            f"</tr>"
        )

    # ── Other sections ──────────────────────────────────────────────────────
    retention_rows = ""
    for w in retention["windows"]:
        cnt = len(retention["buckets"].get(w, []))
        retention_rows += f"<tr><td>{w // 7} weeks</td><td>{cnt}</td></tr>"

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

    rebook_rows = ""
    if rebook.get("by_category"):
        for cat in rebook["by_category"]:
            rebook_rows += f"<tr><td>{cat['category']}</td><td>{cat['avg_weeks']} wks</td><td>{cat['sample_size']}</td></tr>"

    channel_rows = ""
    for ch in booking_src["channels"]:
        channel_rows += f"<tr><td>{ch['channel']}</td><td>{ch['count']}</td><td>{ch['pct']}%</td></tr>"

    booking_link = f'<p><a href="{booking_url}" style="color:#e63946">Book online →</a></p>' if booking_url else ""
    pages_link   = (
        f'<p style="margin-top:24px;text-align:center">'
        f'<a href="{pages_url}" style="background:#1a1a2e;color:#fff;padding:10px 24px;'
        f'border-radius:6px;text-decoration:none;font-size:14px;font-weight:600">'
        f'View your dashboard →</a></p>'
    ) if pages_url else ""

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<style>
  body {{ font-family:Arial,sans-serif; color:#333; max-width:640px; margin:auto; padding:24px; }}
  h1 {{ color:#1a1a2e; margin-bottom:4px; }}
  h2 {{ color:#1a1a2e; border-bottom:2px solid #e63946; padding-bottom:4px; margin-top:28px; }}
  table {{ border-collapse:collapse; width:100%; margin-bottom:16px; }}
  th {{ background:#1a1a2e; color:#fff; padding:6px 10px; text-align:left; font-size:13px; }}
  td {{ padding:5px 10px; border-bottom:1px solid #eee; font-size:13px; }}
  .badge {{ display:inline-block; padding:2px 8px; border-radius:4px; font-size:12px; font-weight:bold; color:#fff; }}
  .badge-low {{ background:#2ecc71; }} .badge-mid {{ background:#3498db; }} .badge-growth {{ background:#e74c3c; }}
</style></head><body>
<h1>Morning Brief — {store_name}</h1>
<p style="color:#888;margin-top:0">Date: <strong>{brief_date}</strong></p>

{section("Chair Utilization")}
{util_boxes}
<table style="margin-top:12px">
  <tr><th>Provider</th><th>30-Day</th><th>7-Day</th><th>3-Day</th><th>Trend</th><th>{brief_date}</th></tr>
  {util_rows if util_rows else '<tr><td colspan="6" style="color:#aaa">No schedule data yet.</td></tr>'}
</table>

{section(f"Lapsed Clients — {retention['total_lapsed']} total")}
<table><tr><th>Window</th><th>Clients</th></tr>{retention_rows}</table>
{booking_link}

{section(f"Risk Appointments — next 3 days ({len(risk)} flagged)")}
<table><tr><th>Date</th><th>Time</th><th>Client</th><th>Risk</th></tr>{risk_rows}</table>

{section(f"Avg Rebook Time — {rebook.get('avg_weeks', '—')} weeks overall")}
{"<table><tr><th>Service Category</th><th>Avg Return</th><th>Sample</th></tr>" + rebook_rows + "</table>" if rebook_rows else "<p style='color:#aaa'>Not enough data yet.</p>"}

{section("Booking Channels — last 7 days")}
{"<table><tr><th>Channel</th><th>Bookings</th><th>Share</th></tr>" + channel_rows + "</table>" if channel_rows else "<p style='color:#aaa'>No channel data yet.</p>"}

{pages_link}
<hr style="margin-top:32px;border:none;border-top:1px solid #eee">
<p style="font-size:11px;color:#aaa">SUN-Agent | Powered by Salon Ultimate data</p>
</body></html>"""
    return html
