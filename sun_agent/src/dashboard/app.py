"""
SUN-Agent Flask web dashboard.
Run:  python3 -m sun_agent.src.dashboard.app
  or: flask --app sun_agent/src/dashboard/app run --port 5050
"""
import json
import os
from datetime import date
from pathlib import Path

from flask import Flask, redirect, render_template, request, url_for, flash

app = Flask(__name__, template_folder="templates")
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "sun-agent-dev-secret")

STORES_ROOT = Path(__file__).resolve().parents[3] / "stores"

EDITABLE_FIELDS = {
    "owner_email":        "Owner Email",
    "owner_phone":        "Owner Phone",
    "twilio_number":      "Twilio Number",
    "booking_url":        "Booking URL",
    "pages_url":          "Dashboard Page URL (Cloudflare Pages)",
    "google_review_url":  "Google Review URL",
    "review_alert_email": "Review Alert Email (negative reviews)",
    "timezone":           "Timezone",
    "brief_frequency":    "Brief Frequency (daily/weekly)",
    "utilization_tier":        "Utilization Tier (low/mid/growth)",
    "slot_duration_minutes":   "Appointment Slot Duration (minutes)",
}


def _all_stores() -> list:
    configs = []
    for p in sorted(STORES_ROOT.glob("*/store_config.json")):
        try:
            cfg = json.loads(p.read_text())
            cfg.setdefault("store_id", p.parent.name)
            configs.append(cfg)
        except Exception:
            pass
    return configs


def _load(store_id: str) -> dict:
    p = STORES_ROOT / store_id / "store_config.json"
    if p.exists():
        cfg = json.loads(p.read_text())
        cfg.setdefault("store_id", store_id)
        return cfg
    return {}


def _save(store_id: str, cfg: dict):
    p = STORES_ROOT / store_id / "store_config.json"
    p.write_text(json.dumps(cfg, indent=2))


def _db_stats(store_id: str) -> dict:
    import sqlite3
    db = STORES_ROOT / store_id / "sun_agent.db"
    if not db.exists():
        return {}
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        clients = conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
        visits = conn.execute("SELECT COUNT(*) FROM client_visits").fetchone()[0]
        last = conn.execute(
            "SELECT ingested_at, report_type, status, row_count_loaded FROM ingestion_log ORDER BY ingested_at DESC LIMIT 1"
        ).fetchone()
        ingestions = conn.execute("SELECT COUNT(*) FROM ingestion_log").fetchone()[0]
        return {
            "clients": clients, "visits": visits, "ingestions": ingestions,
            "last_ingest": dict(last) if last else None,
        }
    finally:
        conn.close()


@app.route("/")
def index():
    stores = _all_stores()
    return render_template("index.html", stores=stores)


@app.route("/poll-email", methods=["POST"])
def poll_email():
    try:
        from src.ingest.email_poller import run_poll
        results = run_poll()
        if not results:
            flash("Checked inbox — no new reports found.", "success")
        else:
            ingested = sum(1 for r in results if r.get("status") == "ok")
            dupes = sum(1 for r in results if r.get("status") == "duplicate")
            errors = sum(1 for r in results if r.get("status") in ("error", "quarantined"))
            parts = []
            if ingested:
                parts.append(f"{ingested} file(s) ingested")
            if dupes:
                parts.append(f"{dupes} already up to date")
            if errors:
                parts.append(f"{errors} error(s)")
            flash(f"Email check complete — {', '.join(parts) or 'nothing new'}.",
                  "success" if not errors else "error")
    except Exception as exc:
        flash(f"Email poll failed: {exc}", "error")
    return redirect(request.referrer or url_for("index"))


@app.route("/stores/<store_id>")
def store_detail(store_id):
    cfg = _load(store_id)
    if not cfg:
        flash(f"Store '{store_id}' not found.", "error")
        return redirect(url_for("index"))
    stats = _db_stats(store_id)

    # Date filter — ?as_of=YYYY-MM-DD, default today
    as_of_str = request.args.get("as_of", "")
    try:
        as_of = date.fromisoformat(as_of_str) if as_of_str else date.today()
    except ValueError:
        as_of = date.today()

    brief_data = None
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
        from src.reporting.brief_builder import build_brief
        brief_data = build_brief(store_id, brief_date=as_of)
    except Exception as exc:
        brief_data = {"error": str(exc)}

    review_stats = None
    try:
        from src.outreach.review_flow import get_review_stats
        review_stats = get_review_stats(store_id, days_back=30)
    except Exception:
        pass

    return render_template("store_detail.html", cfg=cfg, stats=stats, brief=brief_data,
                           review_stats=review_stats, editable_fields=EDITABLE_FIELDS,
                           as_of=str(as_of), today_str=str(date.today()))


@app.route("/stores/<store_id>/edit", methods=["GET", "POST"])
def store_edit(store_id):
    cfg = _load(store_id)
    if not cfg:
        flash(f"Store '{store_id}' not found.", "error")
        return redirect(url_for("index"))

    if request.method == "POST":
        weeks_raw = request.form.get("retention_windows_weeks", "").strip()
        if weeks_raw:
            try:
                cfg["retention_windows_days"] = [int(x.strip()) * 7 for x in weeks_raw.split(",") if x.strip()]
            except ValueError:
                flash("Invalid format for Retention Windows. Use comma-separated numbers.", "error")
        for field in EDITABLE_FIELDS:
            val = request.form.get(field, "").strip()
            cfg[field] = val
        _save(store_id, cfg)

        # Mirror key fields to DB
        _mirror_to_db(store_id, cfg)

        flash("Settings saved.", "success")
        return redirect(url_for("store_detail", store_id=store_id))

    return render_template("store_edit.html", cfg=cfg, editable_fields=EDITABLE_FIELDS)


@app.route("/stores/<store_id>/send-brief", methods=["POST"])
def send_brief_now(store_id):
    try:
        from src.reporting.brief_sender import send_brief
        result = send_brief(store_id)
        if result["status"] == "ok":
            flash("Brief sent successfully.", "success")
        else:
            flash(f"Brief send failed: {result.get('error', 'unknown error')}", "error")
    except Exception as exc:
        flash(f"Error: {exc}", "error")
    return redirect(url_for("store_detail", store_id=store_id))


@app.route("/stores/<store_id>/send-review-texts", methods=["POST"])
def send_review_texts(store_id):
    dry_run = request.form.get("dry_run") == "1"
    try:
        from src.outreach.review_flow import send_review_requests
        results = send_review_requests(store_id, dry_run=dry_run)
        if dry_run:
            lines = []
            for r in results:
                if r.get("status") == "dry_run":
                    lines.append(f"{r['phone']}: \"{r['body']}\"")
                else:
                    lines.append(f"{r.get('phone', r.get('name', '?'))}: {r['status']} — {r.get('reason', '')}")
            preview = " | ".join(lines) if lines else "No clients found for today."
            flash(f"[DRY RUN] {len(lines)} client(s): {preview}", "success")
        else:
            sent = sum(1 for r in results if r.get("status") == "sent")
            skipped = sum(1 for r in results if r.get("status") in ("skipped", "opted_out"))
            flash(f"Review texts: {sent} sent, {skipped} skipped.", "success")
    except Exception as exc:
        flash(f"Error: {exc}", "error")
    return redirect(url_for("store_detail", store_id=store_id))


@app.route("/webhook/sms", methods=["POST"])
def sms_webhook():
    """Twilio inbound SMS webhook — route to correct store by To number."""
    from flask import request as req
    from xml.etree.ElementTree import Element, SubElement, tostring

    to_number = req.form.get("To", "").strip()
    from_phone = req.form.get("From", "").strip()
    body = req.form.get("Body", "").strip()

    # Find store matching the Twilio number
    store_id = None
    for p in sorted(STORES_ROOT.glob("*/store_config.json")):
        try:
            cfg = json.loads(p.read_text())
            if cfg.get("twilio_number", "").strip() == to_number:
                store_id = cfg.get("store_id", p.parent.name)
                break
        except Exception:
            pass

    twiml = Element("Response")
    if store_id:
        from src.outreach.sms_sender import handle_twilio_webhook
        reply = handle_twilio_webhook(store_id, from_phone, body)
        if reply:
            msg_el = SubElement(twiml, "Message")
            msg_el.text = reply
    else:
        app.logger.warning("Inbound SMS to unknown number %s", to_number)

    return app.response_class(
        tostring(twiml, encoding="unicode"),
        mimetype="text/xml",
    )


@app.route("/stores/<store_id>/data")
def store_data(store_id):
    import sqlite3
    db = STORES_ROOT / store_id / "sun_agent.db"
    cfg = _load(store_id)

    date_from = request.args.get("date_from", "")
    date_to   = request.args.get("date_to", "")
    table     = request.args.get("table", "timesheets")

    rows = []
    columns = []
    if db.exists():
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row

        if table == "timesheets":
            q = "SELECT provider_name_raw, work_date, time_in, time_out, hours_worked, is_complete FROM provider_timesheets WHERE store_id=?"
            params = [store_id]
            if date_from:
                q += " AND work_date >= ?"
                params.append(date_from)
            if date_to:
                q += " AND work_date <= ?"
                params.append(date_to)
            q += " ORDER BY work_date, provider_name_raw"
            columns = ["Provider", "Date", "Time In", "Time Out", "Hours", "Complete"]

        elif table == "schedule":
            q = """SELECT provider_name, slot_date,
                          SUM(CASE WHEN slot_state='booked' THEN 1 ELSE 0 END)*1 AS booked,
                          COUNT(*) AS total_slots,
                          ROUND(SUM(CASE WHEN slot_state='booked' THEN 1 ELSE 0 END)*100.0/COUNT(*),1) AS pct
                   FROM provider_schedule_slots WHERE store_id=?"""
            params = [store_id]
            if date_from:
                q += " AND slot_date >= ?"
                params.append(date_from)
            if date_to:
                q += " AND slot_date <= ?"
                params.append(date_to)
            q += " GROUP BY provider_name, slot_date ORDER BY slot_date, provider_name"
            columns = ["Provider", "Date", "Booked Slots", "Total Slots", "% Booked"]

        elif table == "visits":
            q = """SELECT c.client_name_raw, v.visit_date, v.service_category, v.service_description
                   FROM client_visits v JOIN clients c ON c.client_key=v.client_key
                   WHERE v.store_id=?"""
            params = [store_id]
            if date_from:
                q += " AND v.visit_date >= ?"
                params.append(date_from)
            if date_to:
                q += " AND v.visit_date <= ?"
                params.append(date_to)
            q += " ORDER BY v.visit_date DESC LIMIT 200"
            columns = ["Client", "Date", "Category", "Service"]

        elif table == "ingestion":
            q = """SELECT ingested_at, source_filename, report_type, status, row_count_loaded, error_message
                   FROM ingestion_log WHERE 1=1"""
            params = []
            if date_from:
                q += " AND date(ingested_at) >= ?"
                params.append(date_from)
            if date_to:
                q += " AND date(ingested_at) <= ?"
                params.append(date_to)
            q += " ORDER BY ingested_at DESC LIMIT 100"
            columns = ["Ingested At", "Filename", "Report Type", "Status", "Rows Loaded", "Error"]

        rows = [list(r) for r in conn.execute(q, params).fetchall()]
        conn.close()

    return render_template("store_data.html", cfg=cfg, store_id=store_id,
                           rows=rows, columns=columns, table=table,
                           date_from=date_from, date_to=date_to)


@app.route("/stores/<store_id>/ingestion")
def store_ingestion(store_id):
    import sqlite3
    db = STORES_ROOT / store_id / "sun_agent.db"
    rows = []
    if db.exists():
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT ingested_at, source_filename, report_type, status,
                      row_count_raw, row_count_loaded, error_message
               FROM ingestion_log ORDER BY ingested_at DESC LIMIT 50"""
        ).fetchall()
        conn.close()
    cfg = _load(store_id)
    return render_template("ingestion.html", cfg=cfg, rows=[dict(r) for r in rows], store_id=store_id)


def _mirror_to_db(store_id: str, cfg: dict):
    import sqlite3 as _sq
    db = STORES_ROOT / store_id / "sun_agent.db"
    if not db.exists():
        return
    conn = _sq.connect(str(db))
    field_map = {
        "owner_email": "owner_email",
        "owner_phone": "owner_phone",
        "booking_url": "booking_url",
        "timezone": "timezone",
        "twilio_number": "twilio_number",
        "brief_frequency": "brief_frequency",
        "utilization_tier": "utilization_tier",
    }
    for cfg_key, db_col in field_map.items():
        if cfg_key in cfg:
            val = cfg[cfg_key]
            if isinstance(val, list):
                val = json.dumps(val)
            try:
                conn.execute(
                    f"UPDATE store_config SET {db_col}=?, updated_at=datetime('now') WHERE store_id=?",
                    (val, store_id)
                )
            except Exception:
                pass
    conn.commit()
    conn.close()


if __name__ == "__main__":
    app.run(debug=True, port=5050)
