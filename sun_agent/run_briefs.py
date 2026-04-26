#!/usr/bin/env python3
"""
Send morning briefs to all active stores.

Usage:
  python3 run_briefs.py              # all stores
  python3 run_briefs.py <store_id>  # one store only
"""
import logging
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")
sys.path.insert(0, str(Path(__file__).parent))
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

from src.reporting.brief_sender import send_brief, send_all_briefs

if len(sys.argv) > 1:
    store_id = sys.argv[1]
    result = send_brief(store_id)
    print(f"{store_id}: {result}")
else:
    results = send_all_briefs()
    for r in results:
        status = r.get("status", "?")
        sid = r.get("store_id", "?")
        err = f"  ({r['error']})" if r.get("error") else ""
        print(f"  {sid:<40} {status}{err}")
    ok = sum(1 for r in results if r.get("status") == "ok")
    print(f"\nSent {ok}/{len(results)} briefs.")
