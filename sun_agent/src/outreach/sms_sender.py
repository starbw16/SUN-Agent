"""
Twilio SMS outreach for lapsed clients.

Each store has its own Twilio number stored in store_config.twilio_number.
One Twilio account, multiple numbers (~$1/month each).
STOP opt-outs are per-number so they stay isolated per store.

Required env vars:
  TWILIO_ACCOUNT_SID
  TWILIO_AUTH_TOKEN

Per-store: store_config.json → twilio_number (e.g. +16165550100)
"""
import logging
import os
import uuid
from datetime import date

logger = logging.getLogger(__name__)


def _get_twilio_client():
    from twilio.rest import Client
    account_sid = os.environ["TWILIO_ACCOUNT_SID"]
    auth_token = os.environ["TWILIO_AUTH_TOKEN"]
    return Client(account_sid, auth_token)


def _is_opted_out(store_id: str, phone: str) -> bool:
    from ..persistence.store_silo import get_db
    try:
        conn = get_db(store_id)
        row = conn.execute(
            "SELECT 1 FROM sms_opt_outs WHERE store_id=? AND phone=?", (store_id, phone)
        ).fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False


def _record_opt_out(store_id: str, phone: str):
    from ..persistence.store_silo import get_db
    try:
        conn = get_db(store_id)
        conn.execute(
            "INSERT OR IGNORE INTO sms_opt_outs (opt_out_id, store_id, phone) VALUES (?,?,?)",
            (str(uuid.uuid4()), store_id, phone),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def send_retention_sms(store_id: str, dry_run: bool = False) -> list:
    """
    Send retention outreach texts to lapsed clients for one store.
    Only sends to clients whose phone is not opted out.
    Returns list of result dicts.

    dry_run=True: log what would be sent without actually calling Twilio.
    """
    from ..persistence.store_silo import load_store_config
    from ..intelligence.retention import get_lapsed_clients

    config = load_store_config(store_id)
    from_number = config.get("twilio_number", "")
    booking_url = config.get("booking_url", "")
    store_name = config.get("store_name", store_id)

    if not from_number and not dry_run:
        logger.warning("No twilio_number configured for store %s — skipping SMS", store_id)
        return [{"status": "skipped", "reason": "no twilio_number"}]

    lapsed = get_lapsed_clients(store_id, as_of=date.today())
    results = []

    client = None
    if not dry_run:
        try:
            client = _get_twilio_client()
        except Exception as exc:
            return [{"status": "error", "reason": f"Twilio init failed: {exc}"}]

    for window, clients in lapsed["buckets"].items():
        for c in clients:
            phone = c.get("phone", "")
            if not phone:
                continue
            if _is_opted_out(store_id, phone):
                results.append({"phone": phone, "status": "opted_out", "window": window})
                continue

            name = c.get("name", "").split()[0].title() if c.get("name") else "there"
            body = _build_message(name, window, store_name, booking_url)

            if dry_run:
                logger.info("[DRY RUN] Would text %s: %s", phone, body)
                results.append({"phone": phone, "status": "dry_run", "window": window, "body": body})
                continue

            try:
                msg = client.messages.create(
                    body=body,
                    from_=from_number,
                    to=phone,
                )
                logger.info("SMS sent to %s (sid=%s)", phone, msg.sid)
                results.append({"phone": phone, "status": "sent", "window": window, "sid": msg.sid})
            except Exception as exc:
                logger.error("SMS failed to %s: %s", phone, exc)
                results.append({"phone": phone, "status": "error", "window": window, "error": str(exc)})

    return results


def _build_message(first_name: str, window_days: int, store_name: str, booking_url: str) -> str:
    base = f"Hey {first_name}! We miss you at {store_name}. "
    if window_days <= 28:
        base += "It's been a few weeks — time for a fresh cut?"
    elif window_days <= 42:
        base += "It's been a while — your kiddo is probably ready for a trim!"
    else:
        base += "We haven't seen you in over a month — come on back!"
    if booking_url:
        base += f" Book online: {booking_url}"
    base += " Reply STOP to opt out."
    return base


def handle_twilio_webhook(store_id: str, from_phone: str, body: str) -> str | None:
    """
    Called by a webhook handler when an inbound SMS arrives on the store's number.
    Handles STOP opt-outs and review responses.
    Returns a reply body string, or None if no reply needed.
    """
    if body.strip().upper() in ("STOP", "UNSUBSCRIBE", "CANCEL", "QUIT", "END"):
        _record_opt_out(store_id, from_phone)
        logger.info("Opt-out recorded: store=%s phone=%s", store_id, from_phone)
        return None  # Twilio handles STOP reply automatically

    from ..outreach.review_flow import handle_review_response
    reply = handle_review_response(store_id, from_phone, body)
    return reply
