"""
Post-service review flow.

1. send_review_requests()  — finds clients who had a completed slot today,
                             sends a "how was your visit?" SMS, logs to review_requests.
2. handle_review_response() — classifies an inbound reply, routes accordingly:
                              positive → reply with Google review link
                              negative → email owner/alert address + reply with apology
"""
import logging
import os
import smtplib
import uuid
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)

POSITIVE_KEYWORDS = {"5", "4", "great", "good", "awesome", "love", "loved", "amazing",
                     "excellent", "perfect", "wonderful", "fantastic", "best", "happy"}
NEGATIVE_KEYWORDS = {"1", "2", "3", "bad", "poor", "terrible", "horrible", "awful",
                     "disappointed", "unhappy", "worst", "mediocre", "meh", "ok", "okay"}


def _classify(text: str) -> str:
    """Return 'positive', 'negative', or 'unclear' for a reply body."""
    cleaned = text.strip().lower()
    # Exact single-token match first (handles "4", "5", "bad", etc.)
    token = cleaned.split()[0] if cleaned.split() else ""
    if token in POSITIVE_KEYWORDS or any(kw in cleaned for kw in POSITIVE_KEYWORDS):
        return "positive"
    if token in NEGATIVE_KEYWORDS or any(kw in cleaned for kw in NEGATIVE_KEYWORDS):
        return "negative"
    return "unclear"


def send_review_requests(store_id: str, service_date: date = None,
                         dry_run: bool = False) -> list:
    """
    Find clients with a booked slot on service_date, send review SMS to each.
    Skips opted-out phones and phones already texted today.
    """
    from ..persistence.store_silo import get_db, load_store_config
    from ..outreach.sms_sender import _get_twilio_client, _is_opted_out

    service_date = service_date or date.today()
    config = load_store_config(store_id)
    from_number = config.get("twilio_number", "")
    store_name = config.get("store_name", store_id)

    if not from_number and not dry_run:
        return [{"status": "skipped", "reason": "no twilio_number"}]

    conn = get_db(store_id)
    try:
        # Find distinct clients from today's booked slots, joined to phone
        rows = conn.execute(
            """
            SELECT DISTINCT s.client_name_raw, c.primary_phone, c.client_key
            FROM provider_schedule_slots s
            LEFT JOIN clients c
              ON c.store_id = s.store_id
             AND c.client_name_normalized = lower(trim(s.client_name_raw))
            WHERE s.store_id = ?
              AND s.slot_date = ?
              AND s.slot_state = 'booked'
              AND s.client_name_raw IS NOT NULL
            """,
            (store_id, str(service_date)),
        ).fetchall()

        already_sent = set(
            r[0] for r in conn.execute(
                "SELECT phone FROM review_requests WHERE store_id=? AND service_date=?",
                (store_id, str(service_date)),
            ).fetchall()
        )
    finally:
        conn.close()

    results = []
    client_obj = None
    if not dry_run:
        try:
            client_obj = _get_twilio_client()
        except Exception as exc:
            return [{"status": "error", "reason": f"Twilio init failed: {exc}"}]

    for row in rows:
        client_name_raw = row["client_name_raw"] or ""
        phone = row["primary_phone"] or ""
        client_key = row["client_key"] or ""

        if not phone:
            results.append({"name": client_name_raw, "status": "skipped", "reason": "no_phone"})
            continue
        if phone in already_sent:
            results.append({"phone": phone, "status": "skipped", "reason": "already_sent"})
            continue
        if _is_opted_out(store_id, phone):
            results.append({"phone": phone, "status": "opted_out"})
            continue

        first_name = client_name_raw.split()[0].title() if client_name_raw else "there"
        body = (
            f"Hi {first_name}! Thanks for visiting {store_name} today. "
            f"How was your experience? Reply 1-5 (5 = amazing!) "
            f"or just tell us what you think. Reply STOP to opt out."
        )

        if dry_run:
            logger.info("[DRY RUN] Would text %s: %s", phone, body)
            results.append({"phone": phone, "status": "dry_run", "body": body})
            _log_request(store_id, client_key, phone, client_name_raw, service_date, dry_run=True)
            continue

        try:
            msg = client_obj.messages.create(body=body, from_=from_number, to=phone)
            _log_request(store_id, client_key, phone, client_name_raw, service_date)
            results.append({"phone": phone, "status": "sent", "sid": msg.sid})
            logger.info("Review SMS sent to %s (sid=%s)", phone, msg.sid)
        except Exception as exc:
            logger.error("Review SMS failed to %s: %s", phone, exc)
            results.append({"phone": phone, "status": "error", "error": str(exc)})

    return results


def handle_review_response(store_id: str, from_phone: str, body: str) -> str | None:
    """
    Called when an inbound SMS arrives and is identified as a review reply.
    Returns the reply message to send back to the client, or None if not a review reply.
    """
    from ..persistence.store_silo import get_db, load_store_config

    config = load_store_config(store_id)
    google_url = config.get("google_review_url", "")
    alert_email = config.get("review_alert_email") or config.get("owner_email", "")

    conn = get_db(store_id)
    try:
        row = conn.execute(
            """
            SELECT request_id, client_name, service_date
            FROM review_requests
            WHERE store_id = ? AND phone = ? AND outcome = 'pending'
            ORDER BY sent_at DESC LIMIT 1
            """,
            (store_id, from_phone),
        ).fetchone()

        if not row:
            return None  # not a pending review — let caller handle

        request_id = row["request_id"]
        client_name = row["client_name"] or "A client"
        service_date = row["service_date"]
        outcome = _classify(body)

        conn.execute(
            """UPDATE review_requests
               SET response_text=?, response_at=datetime('now'), outcome=?
               WHERE request_id=?""",
            (body, outcome, request_id),
        )
        conn.commit()
    finally:
        conn.close()

    store_name = config.get("store_name", store_id)

    if outcome == "positive":
        reply = f"Thank you so much! We love hearing that. Would you mind leaving us a quick review?"
        if google_url:
            reply += f" {google_url}"
        return reply

    elif outcome == "negative":
        reply = f"We're sorry to hear that! Someone from {store_name} will be in touch shortly."
        if alert_email:
            _send_alert_email(
                alert_email=alert_email,
                store_name=store_name,
                client_name=client_name,
                phone=from_phone,
                service_date=service_date,
                response_text=body,
            )
            _mark_alert_sent(store_id, request_id)
        return reply

    else:
        # Unclear — thank them and still offer review link
        reply = f"Thanks for the feedback! We appreciate it."
        if google_url:
            reply += f" If you'd like to share more, here's our review link: {google_url}"
        return reply


def _log_request(store_id: str, client_key: str, phone: str,
                 client_name: str, service_date: date, dry_run: bool = False):
    from ..persistence.store_silo import get_db
    try:
        conn = get_db(store_id)
        conn.execute(
            """INSERT OR IGNORE INTO review_requests
               (request_id, store_id, client_key, phone, client_name, service_date, outcome)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (str(uuid.uuid4()), store_id, client_key, phone,
             client_name, str(service_date), "dry_run" if dry_run else "pending"),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _send_alert_email(alert_email: str, store_name: str, client_name: str,
                      phone: str, service_date: str, response_text: str):
    gmail_address = os.environ.get("GMAIL_ADDRESS", "")
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "")
    if not gmail_address or not app_password:
        logger.warning("Cannot send alert email — GMAIL_ADDRESS/GMAIL_APP_PASSWORD not set")
        return

    subject = f"[{store_name}] Negative Review Response — {service_date}"
    body = (
        f"A client left a negative review response.\n\n"
        f"Client: {client_name}\n"
        f"Phone:  {phone}\n"
        f"Date:   {service_date}\n\n"
        f"Their message:\n\"{response_text}\"\n\n"
        f"Please follow up directly."
    )
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = gmail_address
    msg["To"] = alert_email
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as server:
            server.ehlo()
            server.starttls()
            server.login(gmail_address, app_password)
            server.sendmail(gmail_address, [alert_email], msg.as_string())
        logger.info("Alert email sent to %s", alert_email)
    except Exception as exc:
        logger.error("Alert email failed: %s", exc)


def _mark_alert_sent(store_id: str, request_id: str):
    from ..persistence.store_silo import get_db
    try:
        conn = get_db(store_id)
        conn.execute("UPDATE review_requests SET alert_sent=1 WHERE request_id=?", (request_id,))
        conn.commit()
        conn.close()
    except Exception:
        pass


def get_review_stats(store_id: str, days_back: int = 30) -> dict:
    """Return review request stats for the dashboard."""
    from ..persistence.store_silo import get_db
    conn = get_db(store_id)
    try:
        rows = conn.execute(
            """
            SELECT outcome, COUNT(*) as cnt
            FROM review_requests
            WHERE store_id = ?
              AND sent_at >= datetime('now', ?)
              AND outcome != 'dry_run'
            GROUP BY outcome
            """,
            (store_id, f"-{days_back} days"),
        ).fetchall()
    finally:
        conn.close()

    counts = {r["outcome"]: r["cnt"] for r in rows}
    total_sent = sum(counts.values())
    responded = counts.get("positive", 0) + counts.get("negative", 0) + counts.get("unclear", 0)
    return {
        "total_sent": total_sent,
        "responded": responded,
        "positive": counts.get("positive", 0),
        "negative": counts.get("negative", 0),
        "unclear": counts.get("unclear", 0),
        "pending": counts.get("pending", 0),
        "response_rate_pct": round(responded / total_sent * 100) if total_sent else 0,
        "positive_pct": round(counts.get("positive", 0) / responded * 100) if responded else 0,
    }
