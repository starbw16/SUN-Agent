#!/usr/bin/env python3
"""
Entry point for the email poller.

Usage:
  python run_poller.py

Environment variables required:
  GMAIL_ADDRESS      - e.g. mwstarback@gmail.com
  GMAIL_APP_PASSWORD - 16-char App Password from Google Account settings

Or pass a .env file path as the first argument:
  python run_poller.py /path/to/.env
"""
import json
import logging
import os
import sys
from pathlib import Path

# Allow running from any directory
sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("sun_agent.poller")


def load_env_file(path: str):
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


if __name__ == "__main__":
    if len(sys.argv) > 1:
        load_env_file(sys.argv[1])

    if not os.environ.get("GMAIL_ADDRESS") or not os.environ.get("GMAIL_APP_PASSWORD"):
        print(
            "ERROR: Set GMAIL_ADDRESS and GMAIL_APP_PASSWORD environment variables.\n"
            "       Generate an App Password at: https://myaccount.google.com/apppasswords\n"
            "       (requires 2-Step Verification enabled)"
        )
        sys.exit(1)

    from src.ingest.email_poller import run_poll

    logger.info("Starting poll cycle...")
    results = run_poll()

    if not results:
        logger.info("No new report emails found.")
    else:
        for r in results:
            status = r.get("status", "unknown")
            store = r.get("store_id", "?")
            rtype = r.get("report_type", "?")
            rows = r.get("rows_loaded", 0)
            fname = r.get("filename", "?")
            if status == "ok":
                logger.info("✓ %s → %s (%s) %d rows loaded", fname, store, rtype, rows)
            elif status == "duplicate":
                logger.info("⟳ %s already ingested (duplicate)", fname)
            else:
                logger.warning("✗ %s: %s — %s", fname, status, r.get("error", ""))

    logger.info("Poll cycle complete. %d file(s) processed.", len(results))
