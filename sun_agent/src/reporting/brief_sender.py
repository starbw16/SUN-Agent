"""
Sends the morning brief via Gmail SMTP (TLS).
Uses GMAIL_ADDRESS + GMAIL_APP_PASSWORD env vars.
Logs sends to brief_log table.
"""
import logging
import os
import smtplib
import uuid
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from ..persistence.store_silo import get_db, load_store_config
from .brief_builder import build_brief
from .brief_formatter import format_brief_text, format_brief_html

logger = logging.getLogger(__name__)


def send_brief(store_id: str, brief_date: date = None,
               gmail_address: str = None, app_password: str = None) -> dict:
    """
    Build and email the morning brief for one store.

    Returns: {"status": "ok"|"error"|"skipped", "error": str (if any)}
    """
    brief_date = brief_date or date.today()
    config = load_store_config(store_id)
    recipient = config.get("owner_email", "")
    if not recipient:
        logger.warning("No owner_email for store %s — skipping brief", store_id)
        return {"status": "skipped", "error": "No owner_email configured"}

    gmail_address = gmail_address or os.environ.get("GMAIL_ADDRESS", "")
    app_password = app_password or os.environ.get("GMAIL_APP_PASSWORD", "")
    if not gmail_address or not app_password:
        return {"status": "error", "error": "GMAIL_ADDRESS or GMAIL_APP_PASSWORD not set"}

    brief = build_brief(store_id, brief_date=brief_date)
    store_name = brief["store_name"]
    subject = f"Morning Brief — {store_name} — {brief_date}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = gmail_address
    msg["To"] = recipient
    msg.attach(MIMEText(format_brief_text(brief), "plain"))
    msg.attach(MIMEText(format_brief_html(brief), "html"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.ehlo()
            server.starttls()
            server.login(gmail_address, app_password)
            server.sendmail(gmail_address, [recipient], msg.as_string())
        logger.info("Brief sent to %s for store=%s date=%s", recipient, store_id, brief_date)
        _log_brief(store_id, brief_date, recipient, "sent")
        return {"status": "ok"}
    except Exception as exc:
        logger.exception("Failed to send brief for store=%s", store_id)
        _log_brief(store_id, brief_date, recipient, "error", str(exc))
        return {"status": "error", "error": str(exc)}


def _log_brief(store_id: str, brief_date: date, recipient: str, status: str, error: str = None):
    try:
        conn = get_db(store_id)
        conn.execute(
            """INSERT OR IGNORE INTO brief_log
               (brief_id, store_id, brief_date, recipient_email, status, error_message)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), store_id, str(brief_date), recipient, status, error),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def send_all_briefs(gmail_address: str = None, app_password: str = None) -> list:
    """
    Send briefs to every active store whose brief_frequency allows a send today.
    Called from cron / run_briefs.py.
    """
    from pathlib import Path
    from ..persistence.store_silo import STORES_ROOT

    results = []
    today = date.today()
    for config_path in sorted(STORES_ROOT.glob("*/store_config.json")):
        import json
        config = json.loads(config_path.read_text())
        store_id = config.get("store_id", config_path.parent.name)
        freq = config.get("brief_frequency", "daily")

        if freq == "daily":
            should_send = True
        elif freq == "weekly":
            should_send = today.weekday() == 0  # Monday
        else:
            should_send = True

        if should_send:
            result = send_brief(store_id, brief_date=today,
                                gmail_address=gmail_address, app_password=app_password)
            result["store_id"] = store_id
            results.append(result)

    return results
