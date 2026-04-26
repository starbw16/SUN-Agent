#!/usr/bin/env python3
"""
SUN-Agent store management CLI.

Commands:
  python3 manage.py stores                        List all stores
  python3 manage.py store <store_id>              Show one store's config
  python3 manage.py set <store_id> <key> <value>  Update a config field
  python3 manage.py add-store                     Add a new store interactively
  python3 manage.py ingestion <store_id>          Show recent ingestion history
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

STORES_ROOT = Path(__file__).parent / "stores"
ROUTING_FILE = Path(__file__).parent / "store_routing.json"

EDITABLE_FIELDS = {
    "owner_email":            "Owner email (morning brief destination)",
    "owner_phone":            "Owner cell phone",
    "twilio_number":          "Twilio number for this store (e.g. +16165550100)",
    "booking_url":            "Online booking URL (included in client outreach texts)",
    "timezone":               "Timezone (e.g. America/New_York)",
    "retention_windows_days": "Comma-separated lapse windows in days (e.g. 28,42,56)",
    "brief_frequency":        "Brief frequency: daily or weekly",
    "utilization_tier":       "Utilization tier: low / mid / growth",
}


def _load_config(store_id: str) -> dict:
    path = STORES_ROOT / store_id / "store_config.json"
    if path.exists():
        return json.loads(path.read_text())
    return {}


def _save_config(store_id: str, config: dict):
    path = STORES_ROOT / store_id / "store_config.json"
    path.write_text(json.dumps(config, indent=2))


def _load_routing() -> dict:
    if ROUTING_FILE.exists():
        return json.loads(ROUTING_FILE.read_text())
    return {"name_patterns": {}, "reply_to_patterns": {}, "store_names": {}}


def _save_routing(routing: dict):
    ROUTING_FILE.write_text(json.dumps(routing, indent=2))


def cmd_stores():
    stores = sorted(STORES_ROOT.glob("*/store_config.json"))
    if not stores:
        print("No stores found. Stores are created automatically when the first report email arrives.")
        return

    print(f"\n{'STORE ID':<35} {'NAME':<45} {'OWNER EMAIL':<40} {'BOOKING URL'}")
    print("-" * 140)
    for p in stores:
        cfg = json.loads(p.read_text())
        sid = cfg.get("store_id", p.parent.name)
        name = cfg.get("store_name", "—")
        email = cfg.get("owner_email", "⚠ not set")
        url = cfg.get("booking_url", "⚠ not set")
        flag = " ✓" if cfg.get("owner_email") and cfg.get("booking_url") else " ⚠"
        print(f"{sid:<35} {name:<45} {email:<40} {url}{flag}")
    print()


def cmd_store(store_id: str):
    cfg = _load_config(store_id)
    if not cfg:
        print(f"Store '{store_id}' not found.")
        return

    print(f"\n── {cfg.get('store_name', store_id)} ──────────────────────────────")
    for key, label in EDITABLE_FIELDS.items():
        val = cfg.get(key, "⚠ not set")
        print(f"  {key:<30} {val}  ({label})")

    # Show DB stats if available
    db_path = STORES_ROOT / store_id / "sun_agent.db"
    if db_path.exists():
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        clients = conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
        visits = conn.execute("SELECT COUNT(*) FROM client_visits").fetchone()[0]
        last = conn.execute(
            "SELECT ingested_at, report_type, status, row_count_loaded FROM ingestion_log ORDER BY ingested_at DESC LIMIT 1"
        ).fetchone()
        conn.close()
        print(f"\n  Clients in DB:  {clients}")
        print(f"  Visit records:  {visits}")
        if last:
            print(f"  Last ingestion: {last[0]} — {last[1]} ({last[2]}, {last[3]} rows)")
    print()


def cmd_set(store_id: str, key: str, value: str):
    if key not in EDITABLE_FIELDS:
        print(f"Unknown field '{key}'. Editable fields: {', '.join(EDITABLE_FIELDS)}")
        return
    cfg = _load_config(store_id)
    if not cfg:
        print(f"Store '{store_id}' not found.")
        return

    if key == "retention_windows_days":
        value = [int(x.strip()) for x in value.split(",")]

    cfg[key] = value
    _save_config(store_id, cfg)

    # Mirror to DB store_config table
    db_path = STORES_ROOT / store_id / "sun_agent.db"
    if db_path.exists():
        import sqlite3, json as _json
        conn = sqlite3.connect(str(db_path))
        if key == "owner_email":
            conn.execute("UPDATE store_config SET owner_email=?, updated_at=datetime('now') WHERE store_id=?", (value, store_id))
        elif key == "owner_phone":
            conn.execute("UPDATE store_config SET owner_phone=?, updated_at=datetime('now') WHERE store_id=?", (value, store_id))
        elif key == "booking_url":
            conn.execute("UPDATE store_config SET booking_url=?, updated_at=datetime('now') WHERE store_id=?", (value, store_id))
        elif key == "timezone":
            conn.execute("UPDATE store_config SET timezone=?, updated_at=datetime('now') WHERE store_id=?", (value, store_id))
        elif key == "retention_windows_days":
            conn.execute("UPDATE store_config SET retention_windows_json=?, updated_at=datetime('now') WHERE store_id=?", (_json.dumps(value), store_id))
        elif key == "twilio_number":
            conn.execute("UPDATE store_config SET twilio_number=?, updated_at=datetime('now') WHERE store_id=?", (value, store_id))
        elif key == "brief_frequency":
            conn.execute("UPDATE store_config SET brief_frequency=?, updated_at=datetime('now') WHERE store_id=?", (value, store_id))
        elif key == "utilization_tier":
            conn.execute("UPDATE store_config SET utilization_tier=?, updated_at=datetime('now') WHERE store_id=?",(value, store_id))
        conn.commit()
        conn.close()

    print(f"✓ {store_id} → {key} = {value}")


def cmd_add_store():
    print("\nAdd a new store")
    print("───────────────")
    store_name = input("Store display name (e.g. Sharkey's Cuts for Kids - Phoenix): ").strip()
    if not store_name:
        print("Cancelled.")
        return

    import re
    store_id = re.sub(r"[^a-z0-9]+", "_", store_name.lower()).strip("_")
    confirm = input(f"Store ID will be '{store_id}' — OK? [Y/n]: ").strip().lower()
    if confirm == "n":
        store_id = input("Enter custom store_id: ").strip()

    owner_email = input("Owner email (for morning brief): ").strip()
    booking_url = input("Booking URL: ").strip()
    name_pattern = input(f"Name pattern to match in Salon Ultimate emails (default: last part of name): ").strip()

    # Create silo
    from src.persistence.store_silo import create_store_silo
    create_store_silo(store_id, store_name, {
        "owner_email": owner_email,
        "booking_url": booking_url,
    })

    # Update routing
    routing = _load_routing()
    if name_pattern:
        routing.setdefault("name_patterns", {})[name_pattern.lower()] = store_id
    routing.setdefault("store_names", {})[store_id] = store_name
    _save_routing(routing)

    print(f"\n✓ Store '{store_id}' created.")
    if not owner_email:
        print("⚠  No owner email set — run: python3 manage.py set {store_id} owner_email their@email.com")
    if not booking_url:
        print("⚠  No booking URL set — run: python3 manage.py set {store_id} booking_url https://...")


def cmd_ingestion(store_id: str):
    db_path = STORES_ROOT / store_id / "sun_agent.db"
    if not db_path.exists():
        print(f"No database for '{store_id}'.")
        return

    import sqlite3
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        """SELECT ingested_at, source_filename, report_type, status,
                  row_count_raw, row_count_loaded, error_message
           FROM ingestion_log ORDER BY ingested_at DESC LIMIT 20"""
    ).fetchall()
    conn.close()

    print(f"\nRecent ingestions for {store_id}:")
    print(f"{'DATE':<22} {'FILE':<45} {'TYPE':<30} {'STATUS':<10} {'RAW':>5} {'LOADED':>6}")
    print("-" * 125)
    for r in rows:
        err = f" ✗ {r[6][:60]}" if r[6] else ""
        print(f"{str(r[0]):<22} {str(r[1]):<45} {str(r[2]):<30} {str(r[3]):<10} {r[4]:>5} {r[5]:>6}{err}")
    print()


COMMANDS = {
    "stores": (cmd_stores, []),
    "store": (cmd_store, ["store_id"]),
    "set": (cmd_set, ["store_id", "key", "value"]),
    "add-store": (cmd_add_store, []),
    "ingestion": (cmd_ingestion, ["store_id"]),
}

if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] not in COMMANDS:
        print(__doc__)
        sys.exit(0)

    cmd_name = args[0]
    fn, params = COMMANDS[cmd_name]
    if len(args) - 1 < len(params):
        print(f"Usage: python3 manage.py {cmd_name} {' '.join('<' + p + '>' for p in params)}")
        sys.exit(1)

    fn(*args[1:1 + len(params)])
