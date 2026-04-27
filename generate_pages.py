#!/usr/bin/env python3
"""
Generates static HTML pages into public/:
  public/index.html                   — public store listing
  public/<store_id>/index.html        — client-facing dashboard (date-filterable)
  public/admin/index.html             — operator overview (all stores, date-filterable)

Run locally after ingesting new data, then push to GitHub.
Cloudflare Pages serves the result.

Usage:
    python3 generate_pages.py              # regenerate all stores + admin
    python3 generate_pages.py grand_rapids_mi   # one store (still rebuilds admin)

Secure the /admin/ path with Cloudflare Access (free tier) to require login.
"""
import json
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent / "sun_agent"))

from src.reporting.brief_builder import build_brief
from src.persistence.store_silo import STORES_ROOT


# ── SVG helpers ───────────────────────────────────────────────────────────────

def _sparkline(values, width=320, height=52, color="#e63946", show_fill=True, svg_id=""):
    if not values or max(values, default=0) == 0:
        id_attr = f' id="{svg_id}"' if svg_id else ""
        return f'<svg width="{width}" height="{height}"{id_attr}></svg>'
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
    fill = f'<polygon id="{svg_id}-fill" points="{area}" fill="{color}" opacity="0.12"/>' if show_fill else ""
    id_attr = f' id="{svg_id}"' if svg_id else ""
    return (
        f'<svg{id_attr} width="{width}" height="{height}" style="display:block;overflow:visible">'
        f'{fill}'
        f'<polyline id="{svg_id}-line" points="{line}" fill="none" stroke="{color}" stroke-width="2"/>'
        f'</svg>'
    )


def _mini_sparkline(values, width=120, height=26, color="#e63946", svg_id=""):
    return _sparkline(values, width=width, height=height, color=color, show_fill=False, svg_id=svg_id)


# ── JS date-range filter (embedded in each store page) ───────────────────────

def _date_filter_js(series: dict, store_id: str) -> str:
    """Return a <script> block that wires up the date range picker."""
    data_json = json.dumps({
        "by_provider": series.get("by_provider", {}),
        "store_daily":  series.get("store_daily", []),
    })
    # Plain string — no f-string — avoids escaping every JS { and }
    js = """
(function() {
const DATA = __DATA__;

function avg(arr) { return arr.length ? arr.reduce((s,x)=>s+x,0)/arr.length : 0; }

function svgPoints(vals, w, h) {
  if (!vals.length) return "";
  const pad=5, n=vals.length, mx=Math.max(...vals,1);
  return vals.map((v,i) => {
    const x = pad + i*(w-pad*2)/Math.max(n-1,1);
    const y = (h-pad) - v/mx*(h-pad*2);
    return x.toFixed(1)+","+y.toFixed(1);
  }).join(" ");
}

function applyRange(start, end) {
  const storeDays  = DATA.store_daily.filter(d => d.date >= start && d.date <= end);
  const storeBooked = storeDays.map(d => d.booked);
  const lineEl = document.getElementById("store-spark-line");
  const fillEl = document.getElementById("store-spark-fill");
  if (lineEl) {
    const pts = svgPoints(storeBooked, 560, 60);
    lineEl.setAttribute("points", pts);
    if (fillEl && pts) {
      const a = pts.split(" ");
      fillEl.setAttribute("points", a[0].split(",")[0]+",60 "+pts+" "+a[a.length-1].split(",")[0]+",60");
    }
  }
  const d0=document.getElementById("store-spark-d0"), d1=document.getElementById("store-spark-d1");
  if (d0 && storeDays.length) d0.textContent = storeDays[0].date;
  if (d1 && storeDays.length) d1.textContent = storeDays[storeDays.length-1].date;

  let totalBooked=0, totalSlots=0;
  for (const [prov, days] of Object.entries(DATA.by_provider)) {
    const filtered = days.filter(d => d.date >= start && d.date <= end);
    if (!filtered.length) continue;
    const pcts   = filtered.map(d => d.pct);
    const avgPct = Math.round(avg(pcts));
    filtered.forEach(d => { totalBooked += d.booked; totalSlots += d.total; });
    const slug  = prov.replace(/[^a-z0-9]/gi,"_").toLowerCase();
    const pctEl = document.getElementById("prov-pct-"+slug);
    if (pctEl) pctEl.textContent = avgPct + "%";
    const spEl  = document.getElementById("spark-"+slug+"-line");
    if (spEl)   spEl.setAttribute("points", svgPoints(pcts, 120, 26));
  }
  const rangeEl=document.getElementById("range-pct"), rangeSub=document.getElementById("range-sub");
  if (rangeEl && storeDays.length) {
    rangeEl.textContent = (totalSlots ? Math.round(totalBooked/totalSlots*100) : 0) + "%";
    if (rangeSub) rangeSub.textContent = start + " – " + end;
  }
}

function init() {
  const allDates = DATA.store_daily.map(d => d.date).sort();
  const minDate  = allDates[0] || "";
  const maxDate  = allDates[allDates.length-1] || "";
  const s = document.getElementById("range-start");
  const e = document.getElementById("range-end");
  if (!s || !e) return;
  s.min=minDate; s.max=maxDate; e.min=minDate; e.max=maxDate;
  s.value = allDates.length > 30 ? allDates[allDates.length-30] : (allDates[0]||"");
  e.value = maxDate;
  const onChange = () => { if (s.value && e.value) applyRange(s.value, e.value); };
  s.addEventListener("change", onChange);
  e.addEventListener("change", onChange);
}

document.addEventListener("DOMContentLoaded", init);
})();
"""
    return "<script>" + js.replace("__DATA__", data_json) + "</script>"


# ── Store page renderer ───────────────────────────────────────────────────────

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
    store_id    = brief["store_id"]
    generated   = datetime.now().strftime("%B %d, %Y at %-I:%M %p")

    # ── Stat boxes ────────────────────────────────────────────────────────────
    pct30 = u30.get("avg_pct") if u30.get("days_on_record") else None
    pct7  = u7.get("avg_pct", 0)
    pct3  = u3.get("avg_pct") if u3.get("days_on_record") else None
    pctd  = ud.get("avg_pct") if ud.get("days_on_record") else None
    diff  = (pct3 - pct7) if pct3 is not None else 0
    tcolor = "#27ae60" if diff >= 5 else ("#e74c3c" if diff <= -5 else "#3498db")
    arrow = "↑" if diff >= 5 else ("↓" if diff <= -5 else "→")
    dpp   = f"+{diff}pp" if diff > 0 else (f"{diff}pp" if diff != 0 else "flat")

    def stat_box(label, value, sub, color="#1a1a2e", val_id="", sub_id=""):
        vid = f' id="{val_id}"' if val_id else ""
        sid = f' id="{sub_id}"' if sub_id else ""
        return (
            f'<div style="text-align:center;padding:14px 20px;background:#f8f9ff;'
            f'border-radius:8px;border:1px solid #e0e0ee;flex:1;min-width:90px">'
            f'<div style="font-size:11px;color:#aaa;text-transform:uppercase;letter-spacing:.06em;margin-bottom:4px">{label}</div>'
            f'<div{vid} style="font-size:28px;font-weight:700;color:{color};line-height:1">{value}</div>'
            f'<div{sid} style="font-size:11px;color:#aaa;margin-top:4px">{sub}</div>'
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
    # Filterable "range" box updated by JS
    stat_boxes += stat_box("Date Range", "—", "select dates below", "#888", val_id="range-pct", sub_id="range-sub")
    stat_boxes += '</div>'

    # ── Store-level sparkline ─────────────────────────────────────────────────
    store_daily = series.get("store_daily", [])
    store_svg = ""
    if len(store_daily) >= 2:
        booked_vals = [d["booked"] for d in store_daily]
        dates = [d["date"] for d in store_daily]
        store_svg = f"""
<div style="background:#fff8f8;border:1px solid #fce4e4;border-radius:8px;padding:14px 16px;margin-bottom:16px">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
    <span style="font-size:12px;color:#888;font-weight:600;text-transform:uppercase;letter-spacing:.05em">Booked Appointments</span>
    <span style="font-size:11px;color:#aaa">drag date range below to filter</span>
  </div>
  {_sparkline(booked_vals, width=560, height=60, svg_id="store-spark")}
  <div style="display:flex;justify-content:space-between;font-size:10px;color:#ccc;margin-top:4px">
    <span id="store-spark-d0">{dates[0]}</span><span id="store-spark-d1">{dates[-1]}</span>
  </div>
</div>"""

    # ── Date range picker ─────────────────────────────────────────────────────
    date_picker = """
<div style="display:flex;align-items:center;gap:12px;margin-bottom:20px;
            background:#f8f9ff;border:1px solid #e0e0ee;border-radius:8px;padding:12px 16px;flex-wrap:wrap">
  <span style="font-size:12px;color:#888;font-weight:600;text-transform:uppercase;letter-spacing:.05em">Date Range</span>
  <label style="font-size:12px;color:#555">From
    <input id="range-start" type="date" style="margin-left:6px;font-size:13px;border:1px solid #ddd;border-radius:4px;padding:3px 6px">
  </label>
  <label style="font-size:12px;color:#555">To
    <input id="range-end" type="date" style="margin-left:6px;font-size:13px;border:1px solid #ddd;border-radius:4px;padding:3px 6px">
  </label>
  <span style="font-size:11px;color:#bbb">Updates utilization stats &amp; sparklines</span>
</div>"""

    # ── Per-provider table ────────────────────────────────────────────────────
    p7_map     = {p["provider"]: p for p in u7.get("by_provider", [])}
    p30_map    = {p["provider"]: p for p in u30.get("by_provider", [])}
    p3_map     = {p["provider"]: p for p in u3.get("by_provider", [])}
    pd_map     = {p["provider"]: p for p in ud.get("by_provider", [])}
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

        slug = prov.replace(" ", "_").replace("'", "").lower()
        prov_series = series_map.get(prov, [])
        spark = _mini_sparkline([p["pct"] for p in prov_series], svg_id=f"spark-{slug}") if len(prov_series) >= 2 else ""

        provider_rows += f"""<tr>
          <td style="font-weight:500">{prov}</td>
          <td style="color:#aaa">{v30}</td>
          <td><strong id="prov-pct-{slug}">{r7['avg_pct']}%</strong></td>
          <td>{v3}</td>
          <td style="color:{tc};font-weight:700;white-space:nowrap">{arr} <span style="font-size:11px;font-weight:400">{dpp_p}</span></td>
          <td>{vd}</td>
          <td style="padding:4px 10px">{spark}</td>
        </tr>"""

    util_table = f"""
<table style="border-collapse:collapse;width:100%;margin-bottom:16px">
  <tr><th>Provider</th><th>30-Day</th><th>7-Day ↔ Range</th><th>3-Day</th>
      <th>Trend</th><th>{brief_date}</th><th>30-Day Sparkline</th></tr>
  {provider_rows or '<tr><td colspan="7" style="color:#aaa">No schedule data yet.</td></tr>'}
</table>"""

    # ── Retention ─────────────────────────────────────────────────────────────
    retention_rows = "".join(
        f"<tr><td>{w // 7} weeks</td><td>{len(retention['buckets'].get(w, []))}</td></tr>"
        for w in retention["windows"]
    )
    book_link = f'<p><a href="{booking_url}" style="color:#e63946">Book online →</a></p>' if booking_url else ""

    # ── Risk ──────────────────────────────────────────────────────────────────
    if risk:
        risk_rows = "".join(
            f'<tr><td>{r["appointment_date"]}</td><td>{r["start_time"] or ""}</td>'
            f'<td>{r["client_name"]}</td>'
            f'<td style="color:{"#c0392b" if r["risk_band"]=="high" else "#e67e22"}">'
            f'{r["cancel_no_show_count"]}x [{r["risk_band"].upper()}]</td></tr>'
            for r in risk
        )
    else:
        risk_rows = '<tr><td colspan="4" style="color:#aaa">None flagged</td></tr>'

    # ── Rebook ────────────────────────────────────────────────────────────────
    rebook_rows = "".join(
        f"<tr><td>{cat['category']}</td><td>{cat['avg_weeks']} wks</td><td>{cat['sample_size']}</td></tr>"
        for cat in rebook.get("by_category", [])
    )

    # ── Booking channels ──────────────────────────────────────────────────────
    channel_rows = "".join(
        f"<tr><td>{ch['channel']}</td><td>{ch['count']}</td><td>{ch['pct']}%</td></tr>"
        for ch in booking_src["channels"]
    )

    def section(title):
        return f'<h2 style="color:#1a1a2e;border-bottom:2px solid #e63946;padding-bottom:6px;margin-top:32px">{title}</h2>'

    filter_js = _date_filter_js(series, store_id)

    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{store_name} — SUN-Agent</title>
<style>
  body {{ font-family:Arial,sans-serif; color:#333; max-width:800px; margin:auto; padding:24px 20px; }}
  h1 {{ color:#1a1a2e; margin-bottom:2px; }}
  h2 {{ color:#1a1a2e; border-bottom:2px solid #e63946; padding-bottom:6px; margin-top:32px; }}
  table {{ border-collapse:collapse; width:100%; margin-bottom:16px; }}
  th {{ background:#1a1a2e; color:#fff; padding:7px 10px; text-align:left; font-size:12px; }}
  td {{ padding:6px 10px; border-bottom:1px solid #eee; font-size:13px; }}
</style>
</head><body>

<h1>{store_name}</h1>
<p style="color:#888;margin-top:0">Report date: <strong>{brief_date}</strong> &nbsp;·&nbsp;
   <span style="font-size:11px;color:#bbb">Last generated: {generated}</span></p>

{section("Chair Utilization")}
{stat_boxes}
{date_picker}
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

{filter_js}
</body></html>"""


# ── Admin page renderer ───────────────────────────────────────────────────────

def render_admin_html(briefs: list[dict]) -> str:
    generated = datetime.now().strftime("%B %d, %Y at %-I:%M %p")

    all_series  = {}
    all_configs = {}
    store_rows  = ""

    for brief in briefs:
        sid    = brief["store_id"]
        config = brief["config"]
        u7     = brief.get("utilization_7", {})
        u3     = brief.get("utilization_3", {})
        ret    = brief["retention"]
        risk   = brief["risk"]
        series = brief.get("utilization_series", {})
        all_series[sid]  = series.get("store_daily", [])
        all_configs[sid] = {
            "store_name":           config.get("store_name", sid),
            "booking_url":          config.get("booking_url", ""),
            "pages_url":            config.get("pages_url", ""),
            "timezone":             config.get("timezone", "America/New_York"),
            "slot_duration_minutes": config.get("slot_duration_minutes", 20),
            "utilization_tier":     config.get("utilization_tier", "mid"),
            "brief_frequency":      config.get("brief_frequency", "daily"),
        }

        pct7 = u7.get("avg_pct", 0) if u7.get("days_on_record") else None
        pct3 = u3.get("avg_pct")    if u3.get("days_on_record") else None
        diff = (pct3 - pct7) if (pct3 is not None and pct7 is not None) else None
        tc   = "#27ae60" if (diff is not None and diff >= 5) else ("#e74c3c" if (diff is not None and diff <= -5) else "#888")
        arr  = ("↑" if diff >= 5 else ("↓" if diff <= -5 else "→")) if diff is not None else "—"
        days = u7.get("days_on_record", 0)

        store_daily = series.get("store_daily", [])
        spark = _mini_sparkline([d["booked"] for d in store_daily], width=100, height=24, svg_id=f"adm-spark-{sid}") if len(store_daily) >= 2 else "—"
        pct7_str  = f'<span id="adm-pct-{sid}">{pct7}%</span>' if pct7 is not None else "—"
        pages_link = f'<a href="{config["pages_url"]}" target="_blank" style="color:#e63946;font-size:12px">Live →</a>' if config.get("pages_url") else '<span style="color:#ccc;font-size:12px">not set</span>'

        store_rows += f"""<tr>
          <td style="font-weight:600">
            <a href="/{sid}/" style="color:#e63946;text-decoration:none">{config.get("store_name", sid)}</a>
            <div style="font-size:11px;color:#aaa">{sid}</div>
          </td>
          <td>{pct7_str}</td>
          <td style="color:{tc};font-weight:600">{arr}</td>
          <td><span id="adm-range-{sid}">—</span></td>
          <td>{ret['total_lapsed']}</td>
          <td>{len(risk)}</td>
          <td style="color:#888">{days}d</td>
          <td style="padding:4px 8px">{spark}</td>
          <td>{pages_link}</td>
          <td>
            <button onclick="openEdit('{sid}')"
              style="background:#1a1a2e;color:#fff;border:none;padding:5px 12px;border-radius:5px;cursor:pointer;font-size:12px">
              Edit
            </button>
          </td>
        </tr>"""

    all_series_json  = json.dumps(all_series)
    all_configs_json = json.dumps(all_configs)
    num_stores = len(briefs)

    _js = """
(function() {
const ALL     = __SERIES__;
const CONFIGS = __CONFIGS__;

// ── Date range filter ────────────────────────────────────────────────────────
function svgPoints(vals, w, h) {
  if (!vals.length) return "";
  const pad=5, n=vals.length, mx=Math.max(...vals,1);
  return vals.map((v,i) => {
    const x = pad+i*(w-pad*2)/Math.max(n-1,1);
    const y = (h-pad)-v/mx*(h-pad*2);
    return x.toFixed(1)+","+y.toFixed(1);
  }).join(" ");
}
function applyRange(start, end) {
  for (const [sid, days] of Object.entries(ALL)) {
    const filtered = days.filter(d => d.date >= start && d.date <= end);
    const booked = filtered.map(d => d.booked);
    const total  = filtered.map(d => d.total || 0);
    const sumB = booked.reduce((s,x)=>s+x,0);
    const sumT = total.reduce((s,x)=>s+x,0);
    const pct  = sumT ? Math.round(sumB/sumT*100) : 0;
    const el = document.getElementById("adm-range-"+sid);
    if (el) el.textContent = filtered.length ? pct+"%" : "—";
    const sp = document.getElementById("adm-spark-"+sid+"-line");
    if (sp) sp.setAttribute("points", svgPoints(booked, 100, 24));
  }
}
function initRange() {
  const allDates = Object.values(ALL).flat().map(d=>d.date).sort();
  const minDate  = allDates[0] || "";
  const maxDate  = allDates[allDates.length-1] || "";
  const s = document.getElementById("adm-start");
  const e = document.getElementById("adm-end");
  if (!s||!e) return;
  s.min=minDate; s.max=maxDate; e.min=minDate; e.max=maxDate;
  s.value = allDates.length>30 ? allDates[allDates.length-30] : (allDates[0]||"");
  e.value = maxDate;
  const onChange = () => { if (s.value&&e.value) applyRange(s.value,e.value); };
  s.addEventListener("change", onChange);
  e.addEventListener("change", onChange);
  if (s.value&&e.value) applyRange(s.value,e.value);
}

// ── Edit modal ───────────────────────────────────────────────────────────────
window.openEdit = function(sid) {
  const cfg = CONFIGS[sid];
  if (!cfg) return;
  document.getElementById("edit-store-id").value   = sid;
  document.getElementById("edit-store-name").value  = cfg.store_name || "";
  document.getElementById("edit-booking-url").value = cfg.booking_url || "";
  document.getElementById("edit-pages-url").value   = cfg.pages_url || "";
  document.getElementById("edit-timezone").value    = cfg.timezone || "";
  document.getElementById("edit-slot-duration").value = cfg.slot_duration_minutes || 20;
  document.getElementById("edit-tier").value        = cfg.utilization_tier || "mid";
  document.getElementById("edit-frequency").value   = cfg.brief_frequency || "daily";
  document.getElementById("edit-status").textContent = "";
  document.getElementById("edit-modal").style.display = "flex";
};

window.closeEdit = function() {
  document.getElementById("edit-modal").style.display = "none";
};

document.addEventListener("DOMContentLoaded", function() {
  initRange();

  document.getElementById("edit-form").addEventListener("submit", async function(e) {
    e.preventDefault();
    const statusEl = document.getElementById("edit-status");
    statusEl.style.color = "#888";
    statusEl.textContent = "Saving…";

    const sid    = document.getElementById("edit-store-id").value;
    const secret = document.getElementById("edit-secret").value;
    const payload = {
      store_id:              sid,
      store_name:            document.getElementById("edit-store-name").value,
      booking_url:           document.getElementById("edit-booking-url").value,
      pages_url:             document.getElementById("edit-pages-url").value,
      timezone:              document.getElementById("edit-timezone").value,
      slot_duration_minutes: parseInt(document.getElementById("edit-slot-duration").value) || 20,
      utilization_tier:      document.getElementById("edit-tier").value,
      brief_frequency:       document.getElementById("edit-frequency").value,
    };

    try {
      const res = await fetch("/api/update-store", {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Admin-Secret": secret },
        body: JSON.stringify(payload),
      });
      const data = await res.json();
      if (res.ok) {
        statusEl.style.color = "#27ae60";
        statusEl.textContent = "Saved! Changes take effect after next generate_pages.py run.";
        // Update local CONFIGS so modal reflects new values
        CONFIGS[sid] = { ...CONFIGS[sid], ...payload };
      } else {
        statusEl.style.color = "#e74c3c";
        statusEl.textContent = "Error: " + (data.error || res.statusText);
      }
    } catch(err) {
      statusEl.style.color = "#e74c3c";
      statusEl.textContent = "Network error: " + err.message;
    }
  });
});
})();
"""

    js_block = ("<script>" +
                _js.replace("__SERIES__", all_series_json)
                   .replace("__CONFIGS__", all_configs_json) +
                "</script>")

    modal = """
<div id="edit-modal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.5);
     z-index:1000;align-items:center;justify-content:center">
  <div style="background:#fff;border-radius:10px;padding:28px 32px;width:480px;max-width:95vw;
              max-height:90vh;overflow-y:auto;box-shadow:0 8px 40px rgba(0,0,0,.2)">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px">
      <h2 style="margin:0;font-size:18px;color:#1a1a2e">Edit Store</h2>
      <button onclick="closeEdit()" style="background:none;border:none;font-size:22px;cursor:pointer;color:#888">&times;</button>
    </div>
    <form id="edit-form">
      <input type="hidden" id="edit-store-id">
      <label style="font-size:12px;font-weight:600;color:#555;display:block;margin-bottom:2px">Store Name</label>
      <input id="edit-store-name" type="text" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:5px;font-size:14px;margin-bottom:14px">
      <label style="font-size:12px;font-weight:600;color:#555;display:block;margin-bottom:2px">Booking URL</label>
      <input id="edit-booking-url" type="text" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:5px;font-size:14px;margin-bottom:14px" placeholder="https://...">
      <label style="font-size:12px;font-weight:600;color:#555;display:block;margin-bottom:2px">Dashboard Page URL (Cloudflare Pages)</label>
      <input id="edit-pages-url" type="text" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:5px;font-size:14px;margin-bottom:14px" placeholder="https://sun-agent.pages.dev/store_id">
      <label style="font-size:12px;font-weight:600;color:#555;display:block;margin-bottom:2px">Timezone</label>
      <input id="edit-timezone" type="text" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:5px;font-size:14px;margin-bottom:14px" placeholder="America/New_York">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px">
        <div>
          <label style="font-size:12px;font-weight:600;color:#555;display:block;margin-bottom:2px">Slot Duration (min)</label>
          <input id="edit-slot-duration" type="number" min="5" max="60" step="5" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:5px;font-size:14px">
        </div>
        <div>
          <label style="font-size:12px;font-weight:600;color:#555;display:block;margin-bottom:2px">Utilization Tier</label>
          <select id="edit-tier" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:5px;font-size:14px">
            <option value="low">Low (&lt;40%)</option>
            <option value="mid">Mid (40-70%)</option>
            <option value="growth">Growth (&gt;70%)</option>
          </select>
        </div>
      </div>
      <label style="font-size:12px;font-weight:600;color:#555;display:block;margin-bottom:2px">Brief Frequency</label>
      <select id="edit-frequency" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:5px;font-size:14px;margin-bottom:14px">
        <option value="daily">Daily</option>
        <option value="weekly">Weekly</option>
      </select>
      <label style="font-size:12px;font-weight:600;color:#555;display:block;margin-bottom:2px">Admin Secret</label>
      <input id="edit-secret" type="password" style="width:100%;padding:8px;border:1px solid #ddd;border-radius:5px;font-size:14px;margin-bottom:20px" placeholder="ADMIN_SECRET env variable">
      <div style="display:flex;gap:10px;align-items:center">
        <button type="submit" style="background:#1a1a2e;color:#fff;border:none;padding:9px 22px;border-radius:6px;font-size:14px;font-weight:600;cursor:pointer">Save</button>
        <button type="button" onclick="closeEdit()" style="background:#eee;color:#333;border:none;padding:9px 22px;border-radius:6px;font-size:14px;cursor:pointer">Cancel</button>
        <span id="edit-status" style="font-size:12px;color:#888;margin-left:8px"></span>
      </div>
    </form>
  </div>
</div>"""

    no_stores = '<tr><td colspan="10" style="color:#aaa">No stores ingested yet.</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>SUN-Agent Admin</title>
<style>
  body {{ font-family:Arial,sans-serif; color:#333; max-width:1000px; margin:auto; padding:28px 20px; }}
  h1 {{ color:#1a1a2e; margin-bottom:2px; }}
  table {{ border-collapse:collapse; width:100%; margin-top:16px; }}
  th {{ background:#1a1a2e; color:#fff; padding:8px 10px; text-align:left; font-size:12px; }}
  td {{ padding:7px 10px; border-bottom:1px solid #eee; font-size:13px; vertical-align:middle; }}
  tr:hover td {{ background:#fafafa; }}
</style>
</head><body>

{modal}

<h1>SUN-Agent Admin</h1>
<p style="color:#888;margin-top:0;font-size:13px">
  {num_stores} store(s) &nbsp;·&nbsp; Last generated: {generated}
  &nbsp;·&nbsp; <a href="/" style="color:#e63946;font-size:12px">← Public index</a>
</p>
<p style="font-size:11px;color:#bbb;margin-top:-8px">
  Protect this page with
  <a href="https://developers.cloudflare.com/cloudflare-one/policies/access/" style="color:#e63946">Cloudflare Access</a>.
  Set <code>GITHUB_TOKEN</code>, <code>GITHUB_REPO</code>, and <code>ADMIN_SECRET</code> in CF Pages env variables to enable editing.
</p>

<div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;
            background:#f8f9ff;border:1px solid #e0e0ee;border-radius:8px;padding:12px 16px;flex-wrap:wrap">
  <span style="font-size:12px;color:#888;font-weight:600;text-transform:uppercase;letter-spacing:.05em">Date Range</span>
  <label style="font-size:12px;color:#555">From
    <input id="adm-start" type="date" style="margin-left:6px;font-size:13px;border:1px solid #ddd;border-radius:4px;padding:3px 6px">
  </label>
  <label style="font-size:12px;color:#555">To
    <input id="adm-end" type="date" style="margin-left:6px;font-size:13px;border:1px solid #ddd;border-radius:4px;padding:3px 6px">
  </label>
  <span style="font-size:11px;color:#bbb">Updates "Range %" and sparklines</span>
</div>

<table>
  <tr>
    <th>Store</th><th>7-Day Util</th><th>3-Day Trend</th><th>Range %</th>
    <th>Lapsed</th><th>Risk</th><th>Data</th><th>Bookings</th><th>Live Page</th><th></th>
  </tr>
  {store_rows or no_stores}
</table>

<hr style="margin-top:40px;border:none;border-top:1px solid #eee">
<p style="font-size:11px;color:#aaa">SUN-Agent | Powered by Salon Ultimate data</p>

{js_block}
</body></html>"""


# ── Public landing index ──────────────────────────────────────────────────────

def render_index_html(stores: list[dict]) -> str:
    rows = "".join(
        f'<li style="margin:12px 0">'
        f'<a href="/{s["store_id"]}/" style="font-size:16px;color:#e63946;font-weight:600">{s["store_name"]}</a>'
        f'</li>'
        for s in stores
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
    return [p.name for p in sorted(STORES_ROOT.iterdir()) if (p / "sun_agent.db").exists()]


def main():
    target_ids = sys.argv[1:] or list_store_ids()
    if not target_ids:
        print("No stores found. Ingest data first.")
        sys.exit(1)

    public = Path(__file__).parent / "public"
    store_meta = []
    all_briefs = []

    for store_id in target_ids:
        print(f"Building {store_id}...", end=" ", flush=True)
        try:
            brief = build_brief(store_id)
            html  = render_page_html(brief)
            out   = public / store_id / "index.html"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(html, encoding="utf-8")
            store_meta.append({"store_id": store_id, "store_name": brief["store_name"]})
            all_briefs.append(brief)
            print("✓")
        except Exception as e:
            print(f"ERROR: {e}")

    # Admin page (always rebuilt with all known stores)
    if all_briefs:
        admin_html = render_admin_html(all_briefs)
        (public / "admin").mkdir(parents=True, exist_ok=True)
        (public / "admin" / "index.html").write_text(admin_html, encoding="utf-8")
        print("Admin page  ✓")

    # Public landing index
    (public / "index.html").write_text(render_index_html(store_meta), encoding="utf-8")

    print(f"\nDone — push to GitHub to deploy via Cloudflare Pages.")
    print(f"  Client pages: /<store_id>/")
    print(f"  Admin page:   /admin/  (protect with Cloudflare Access)")


if __name__ == "__main__":
    main()
