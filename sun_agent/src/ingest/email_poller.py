"""
Gmail IMAP poller for Salon Ultimate report emails.

Watches for emails from mailer@salonultimate.email (or subject "Report Mailing System"),
downloads .xls/.xlsx attachments, routes to the correct store via display name,
and calls the ingest pipeline.

Setup:
  1. Enable IMAP in Gmail settings (Settings → See all settings → Forwarding and POP/IMAP)
  2. Generate a Gmail App Password (Google Account → Security → 2-Step Verification → App Passwords)
  3. Set env vars: GMAIL_ADDRESS, GMAIL_APP_PASSWORD
     or pass them directly to EmailPoller(email=..., password=...)
"""
import email
import imaplib
import logging
import os
import tempfile
from email.header import decode_header
from pathlib import Path

from .email_router import resolve_store_from_email
from .pipeline import ingest_file

logger = logging.getLogger(__name__)

SALON_ULTIMATE_SENDER = "salonultimate.email"
SALON_ULTIMATE_SUBJECT = "Report Mailing System"
EXCEL_EXTENSIONS = {".xls", ".xlsx", ".csv"}
PROCESSED_LABEL = "sun-agent-processed"


def _decode_str(value) -> str:
    """Decode encoded email header strings."""
    if value is None:
        return ""
    parts = decode_header(value)
    result = []
    for part, encoding in parts:
        if isinstance(part, bytes):
            result.append(part.decode(encoding or "utf-8", errors="replace"))
        else:
            result.append(str(part))
    return " ".join(result).strip()


class EmailPoller:
    def __init__(self, gmail_address: str = None, app_password: str = None):
        self.address = gmail_address or os.environ["GMAIL_ADDRESS"]
        self.password = app_password or os.environ["GMAIL_APP_PASSWORD"]

    def _connect(self) -> imaplib.IMAP4_SSL:
        conn = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        conn.login(self.address, self.password)
        return conn

    def poll(self) -> list:
        """
        Check inbox for unprocessed Salon Ultimate emails.
        Returns list of ingest result dicts.
        """
        results = []
        conn = self._connect()
        try:
            conn.select("INBOX")
            message_ids = self._search_unprocessed(conn)
            logger.info("Found %d unprocessed Salon Ultimate emails", len(message_ids))

            for msg_id in message_ids:
                result = self._process_message(conn, msg_id)
                results.extend(result)

        finally:
            try:
                conn.logout()
            except Exception:
                pass

        return results

    def _search_unprocessed(self, conn: imaplib.IMAP4_SSL) -> list:
        """Find emails from Salon Ultimate or configured owner addresses."""
        # Search by subject — most reliable signal we have
        _, data = conn.search(
            None,
            f'(SUBJECT "{SALON_ULTIMATE_SUBJECT}" UNSEEN)'
        )
        by_subject = set(data[0].split()) if data[0] else set()

        # Also search by sender domain as a backup
        _, data2 = conn.search(
            None,
            f'(FROM "{SALON_ULTIMATE_SENDER}" UNSEEN)'
        )
        by_sender = set(data2[0].split()) if data2[0] else set()

        # Also search for emails from any configured owner email (for ad-hoc/test sends).
        # No UNSEEN filter here — owner may have opened the email; rely on file-hash dedup.
        by_owner = set()
        for owner_email in self._owner_emails():
            _, data3 = conn.search(None, f'(FROM "{owner_email}")')
            if data3[0]:
                by_owner.update(data3[0].split())

        combined = by_subject | by_sender | by_owner
        return list(combined)

    def _owner_emails(self) -> list:
        """Return all unique owner_email values across store configs."""
        from pathlib import Path
        from ..persistence.store_silo import STORES_ROOT
        emails = []
        for p in STORES_ROOT.glob("*/store_config.json"):
            try:
                import json
                cfg = json.loads(p.read_text())
                email = cfg.get("owner_email", "").strip()
                if email and email not in emails:
                    emails.append(email)
            except Exception:
                pass
        return emails

    def _process_message(self, conn: imaplib.IMAP4_SSL, msg_id: bytes) -> list:
        """Download attachments from one message and ingest them."""
        _, msg_data = conn.fetch(msg_id, "(RFC822)")
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)

        from_raw = _decode_str(msg.get("From", ""))
        reply_to_raw = _decode_str(msg.get("Reply-To", ""))
        subject = _decode_str(msg.get("Subject", ""))

        # Extract display name from From header (before the <email> part)
        display_name = from_raw.split("<")[0].strip().strip('"')

        # Extract reply-to email address
        reply_to_email = ""
        if "<" in reply_to_raw:
            reply_to_email = reply_to_raw.split("<")[1].rstrip(">").strip()
        else:
            reply_to_email = reply_to_raw.strip()

        # Extract actual From email address
        from_email_addr = ""
        if "<" in from_raw:
            from_email_addr = from_raw.split("<")[1].rstrip(">").strip()
        else:
            from_email_addr = from_raw.strip()

        # Owner email check first — prevents display name last-resort from winning
        store_id, store_name = _resolve_by_owner_email(from_email_addr)

        # Fall back to display name / reply-to routing (Salon Ultimate emails)
        if not store_id:
            store_id, store_name = resolve_store_from_email(display_name, reply_to_email)

        logger.info(
            "Processing email: from='%s' reply_to='%s' → store_id='%s'",
            display_name, reply_to_email, store_id,
        )

        # Auto-save owner email from reply-to on first contact
        if store_id and reply_to_email:
            _update_owner_email(store_id, store_name, reply_to_email)

        results = []
        attachments_found = False

        with tempfile.TemporaryDirectory() as tmpdir:
            for part in msg.walk():
                content_disposition = str(part.get("Content-Disposition", ""))
                if "attachment" not in content_disposition:
                    continue

                filename_raw = part.get_filename()
                if not filename_raw:
                    continue
                filename = _decode_str(filename_raw)
                ext = Path(filename).suffix.lower()

                if ext not in EXCEL_EXTENSIONS:
                    logger.debug("Skipping non-Excel attachment: %s", filename)
                    continue

                attachments_found = True
                attachment_path = Path(tmpdir) / filename
                attachment_path.write_bytes(part.get_payload(decode=True))

                logger.info("Ingesting attachment: %s (store=%s)", filename, store_id)
                result = ingest_file(
                    attachment_path,
                    store_id=store_id,
                    store_name=store_name,
                )
                result["source_email"] = display_name
                result["filename"] = filename
                results.append(result)

        if attachments_found:
            # Mark as read so we don't reprocess
            conn.store(msg_id, "+FLAGS", "\\Seen")
            logger.info("Marked email %s as read", msg_id)
        else:
            logger.warning(
                "No Excel attachments found in email from '%s' (subject: %s)",
                display_name, subject,
            )
            conn.store(msg_id, "+FLAGS", "\\Seen")

        return results


def _resolve_by_owner_email(from_email: str) -> tuple:
    """Check if from_email matches any store's owner_email and return (store_id, store_name)."""
    from pathlib import Path
    from ..persistence.store_silo import STORES_ROOT
    import json
    from_lower = from_email.lower().strip()
    for p in sorted(STORES_ROOT.glob("*/store_config.json")):
        try:
            cfg = json.loads(p.read_text())
            if cfg.get("owner_email", "").lower().strip() == from_lower:
                store_id = cfg.get("store_id", p.parent.name)
                store_name = cfg.get("store_name", store_id)
                logger.info("Resolved store %s via owner_email match (%s)", store_id, from_email)
                return store_id, store_name
        except Exception:
            pass
    return None, None


def _update_owner_email(store_id: str, store_name: str, owner_email: str):
    """Persist owner_email into store_config.json and DB if not already set."""
    from ..persistence.store_silo import create_store_silo, get_store_path, load_store_config
    import json
    create_store_silo(store_id, store_name)
    config_path = get_store_path(store_id) / "store_config.json"
    config = load_store_config(store_id)
    if not config.get("owner_email"):
        config["owner_email"] = owner_email
        config_path.write_text(json.dumps(config, indent=2))
        logger.info("Saved owner_email=%s for store=%s", owner_email, store_id)


def run_poll(gmail_address: str = None, app_password: str = None) -> list:
    """Convenience entry point for cron/scheduled use."""
    poller = EmailPoller(gmail_address=gmail_address, app_password=app_password)
    return poller.poll()
