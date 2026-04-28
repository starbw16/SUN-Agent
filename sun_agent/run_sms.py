#!/usr/bin/env python3
"""
Send SMS outreach for all active stores.

Usage:
  python3 run_sms.py                         # retention + review, dry-run
  python3 run_sms.py --live                  # retention + review, live send
  python3 run_sms.py retention               # retention only, dry-run
  python3 run_sms.py retention --live        # retention only, live send
  python3 run_sms.py review                  # review requests only, dry-run
  python3 run_sms.py review --live           # review requests only, live send
  python3 run_sms.py retention <store_id>    # one store only

Retention SMS runs in the evening (~6–8 PM) so clients see it after work.
Review requests run at end of day after the last appointment.

Add to crontab for daily automation:
  0 19 * * * cd /path/to/sun_agent && python3 run_sms.py retention --live
  0 20 * * * cd /path/to/sun_agent && python3 run_sms.py review --live
"""
import logging
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
sys.path.insert(0, str(Path(__file__).parent))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from src.persistence.store_silo import STORES_ROOT
import json

args = sys.argv[1:]
mode = "both"
live = "--live" in args
store_filter = None

positional = [a for a in args if not a.startswith("--")]
if positional:
    if positional[0] in ("retention", "review", "both"):
        mode = positional[0]
        if len(positional) > 1:
            store_filter = positional[1]
    else:
        store_filter = positional[0]

dry_run = not live

if dry_run:
    print("DRY RUN — no messages will be sent. Pass --live to send for real.\n")


def get_store_ids():
    ids = []
    for p in sorted(STORES_ROOT.glob("*/store_config.json")):
        cfg = json.loads(p.read_text())
        sid = cfg.get("store_id", p.parent.name)
        if not store_filter or sid == store_filter:
            ids.append(sid)
    return ids


def run_retention(store_ids):
    from src.outreach.sms_sender import send_retention_sms
    print("── Retention Outreach ──────────────────────────────")
    for sid in store_ids:
        print(f"\n  {sid}")
        results = send_retention_sms(sid, dry_run=dry_run)
        counts = {}
        for r in results:
            s = r.get("status", "?")
            counts[s] = counts.get(s, 0) + 1
        for status, count in sorted(counts.items()):
            print(f"    {status:<12} {count}")


def run_review(store_ids):
    from src.outreach.review_flow import send_review_requests
    print("── Review Requests ─────────────────────────────────")
    for sid in store_ids:
        print(f"\n  {sid}")
        results = send_review_requests(sid, dry_run=dry_run)
        counts = {}
        for r in results:
            s = r.get("status", "?")
            counts[s] = counts.get(s, 0) + 1
        for status, count in sorted(counts.items()):
            print(f"    {status:<12} {count}")


store_ids = get_store_ids()
if not store_ids:
    print("No stores found.")
    sys.exit(0)

if mode in ("retention", "both"):
    run_retention(store_ids)
if mode in ("review", "both"):
    run_review(store_ids)

print("\nDone.")
