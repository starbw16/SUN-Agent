"""
Twilio SMS outreach for lapsed clients.

Each store has its own Twilio number stored in store_config.twilio_number.
One Twilio account, multiple numbers (~$1/month each).
STOP opt-outs are per-number so they stay isolated per store.

Message rotation: each client gets a different template every time they're
contacted for the same lapse tier, cycling through ~10 messages so they
never receive the same text twice in a row.

Required env vars:
  TWILIO_ACCOUNT_SID
  TWILIO_AUTH_TOKEN

Per-store: store_config.json → twilio_number (e.g. +16165550100)
"""
import logging
import os
import uuid
from datetime import date, datetime

from .message_templates import get_templates, render

logger = logging.getLogger(__name__)


def _get_twilio_client():
    from twilio.rest import Client
    return Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])


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


def _next_message_index(store_id: str, phone: str, window_days: int,
                        num_templates: int, conn) -> int:
    """Return the next template index to use for this phone + window combination."""
    row = conn.execute(
        """SELECT message_index FROM lapsed_outreach_log
           WHERE store_id=? AND phone=? AND window_days=? AND status != 'failed'
           ORDER BY sent_at DESC LIMIT 1""",
        (store_id, phone, window_days),
    ).fetchone()
    if row is None:
        return 0
    return (row["message_index"] + 1) % num_templates


def _log_outreach(conn, store_id: str, client_key: str, phone: str,
                  window_days: int, message_index: int, body: str, status: str):
    conn.execute(
        """INSERT INTO lapsed_outreach_log
           (log_id, store_id, client_key, phone, window_days, message_index,
            message_body, status, sent_at)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (str(uuid.uuid4()), store_id, client_key, phone, window_days,
         message_index, body, status, datetime.now().isoformat()),
    )
    conn.commit()


def send_retention_sms(store_id: str, dry_run: bool = False) -> list:
    """
    Send retention outreach texts to all lapsed clients for one store.
    Each client receives the next message in their rotation — they never
    get the same template twice in a row for the same lapse tier.

    dry_run=True: logs what would be sent without calling Twilio.
    Returns a list of result dicts.
    """
    from ..persistence.store_silo import load_store_config, get_db
    from ..intelligence.retention import get_lapsed_clients

    config = load_store_config(store_id)
    from_number = config.get("twilio_number", "")
    booking_url = config.get("booking_url", "")
    store_name  = config.get("store_name", store_id)

    if not from_number and not dry_run:
        logger.warning("No twilio_number configured for store %s — skipping SMS", store_id)
        return [{"status": "skipped", "reason": "no twilio_number"}]

    lapsed = get_lapsed_clients(store_id, as_of=date.today())
    results = []

    twilio_client = None
    if not dry_run:
        try:
            twilio_client = _get_twilio_client()
        except Exception as exc:
            return [{"status": "error", "reason": f"Twilio init failed: {exc}"}]

    conn = get_db(store_id)
    try:
        for window_days, clients in lapsed["buckets"].items():
            templates = get_templates(window_days)
            num_templates = len(templates)

            for c in clients:
                phone      = c.get("phone", "")
                client_key = c.get("client_key", "")
                name_raw   = c.get("name", "")

                if not phone:
                    results.append({"name": name_raw, "status": "skipped", "reason": "no_phone", "window": window_days})
                    continue
                if _is_opted_out(store_id, phone):
                    results.append({"phone": phone, "status": "opted_out", "window": window_days})
                    continue

                first_name    = name_raw.split()[0].title() if name_raw else "there"
                msg_index     = _next_message_index(store_id, phone, window_days, num_templates, conn)
                body          = render(templates[msg_index], first_name, store_name, booking_url)

                if dry_run:
                    logger.info("[DRY RUN] window=%dd template=%d/%d to=%s: %s",
                                window_days, msg_index + 1, num_templates, phone, body)
                    _log_outreach(conn, store_id, client_key, phone, window_days, msg_index, body, "dry_run")
                    results.append({
                        "phone": phone, "status": "dry_run", "window": window_days,
                        "template": msg_index + 1, "body": body,
                    })
                    continue

                try:
                    msg = twilio_client.messages.create(body=body, from_=from_number, to=phone)
                    _log_outreach(conn, store_id, client_key, phone, window_days, msg_index, body, "sent")
                    logger.info("Retention SMS sent to %s (sid=%s, window=%dd, template=%d/%d)",
                                phone, msg.sid, window_days, msg_index + 1, num_templates)
                    results.append({
                        "phone": phone, "status": "sent", "window": window_days,
                        "template": msg_index + 1, "sid": msg.sid,
                    })
                except Exception as exc:
                    _log_outreach(conn, store_id, client_key, phone, window_days, msg_index, body, "failed")
                    logger.error("SMS failed to %s: %s", phone, exc)
                    results.append({"phone": phone, "status": "error", "window": window_days, "error": str(exc)})
    finally:
        conn.close()

    return results


def handle_twilio_webhook(store_id: str, from_phone: str, body: str) -> str | None:
    """
    Called by the webhook handler when an inbound SMS arrives on the store's number.
    Handles STOP opt-outs and review responses.
    Returns a reply body string, or None if no reply needed.
    """
    if body.strip().upper() in ("STOP", "UNSUBSCRIBE", "CANCEL", "QUIT", "END"):
        _record_opt_out(store_id, from_phone)
        logger.info("Opt-out recorded: store=%s phone=%s", store_id, from_phone)
        return None  # Twilio handles the STOP reply automatically

    from ..outreach.review_flow import handle_review_response
    return handle_review_response(store_id, from_phone, body)
