"""
Creates and manages per-store folder silos and their SQLite databases.
"""
import json
import sqlite3
from pathlib import Path

from .schema import init_schema


STORES_ROOT  = Path(__file__).resolve().parents[3] / "stores"
CONFIG_ROOT  = Path(__file__).resolve().parents[3] / "config"


def get_store_path(store_id: str) -> Path:
    return STORES_ROOT / store_id


def create_store_silo(store_id: str, store_name: str, config_overrides: dict = None) -> Path:
    """
    Create store directory structure, SQLite DB, and default store_config.json.
    Idempotent — safe to call repeatedly.
    """
    store_path = get_store_path(store_id)
    for sub in ("raw", "normalized", "exports", "logs"):
        (store_path / sub).mkdir(parents=True, exist_ok=True)

    db_path = store_path / "sun_agent.db"
    conn = init_schema(str(db_path))

    conn.execute(
        """INSERT OR IGNORE INTO stores (store_id, store_name, source_store_name)
           VALUES (?, ?, ?)""",
        (store_id, store_name, store_name),
    )
    conn.execute(
        "INSERT OR IGNORE INTO store_config (store_id) VALUES (?)", (store_id,)
    )
    conn.commit()
    conn.close()

    config_path = store_path / "store_config.json"
    if not config_path.exists():
        default_config = {
            "store_id": store_id,
            "store_name": store_name,
            "booking_url": "",
            "pages_url": "",
            "owner_email": "",
            "owner_phone": "",
            "twilio_number": "",
            "timezone": "America/New_York",
            "retention_windows_days": [28, 42, 56],
            "risk_thresholds": {"high": 3, "medium": 1},
            "utilization_threshold": 0.80,
            "utilization_tier": "mid",
            "brief_frequency": "daily",
        }
        if config_overrides:
            default_config.update(config_overrides)
        config_path.write_text(json.dumps(default_config, indent=2))

    return store_path


def get_db(store_id: str) -> sqlite3.Connection:
    db_path = get_store_path(store_id) / "sun_agent.db"
    if not db_path.exists():
        raise FileNotFoundError(f"No database for store '{store_id}'. Run create_store_silo first.")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def load_store_config(store_id: str) -> dict:
    """
    Load config, merging repo config/  (non-sensitive, tracked in git) with
    the local stores/ config (has sensitive fields like owner_email/phone).
    Local store config takes precedence on any key conflict.
    """
    cfg = {}
    repo_config_path = CONFIG_ROOT / f"{store_id}.json"
    if repo_config_path.exists():
        cfg.update(json.loads(repo_config_path.read_text()))
    local_config_path = get_store_path(store_id) / "store_config.json"
    if local_config_path.exists():
        cfg.update(json.loads(local_config_path.read_text()))
    return cfg


def resolve_store_id(store_name_raw: str) -> str:
    """Convert a raw store name from report metadata into a filesystem-safe store_id."""
    import re
    name = store_name_raw.strip().lower()
    name = re.sub(r"[^a-z0-9]+", "_", name).strip("_")
    return name or "unknown_store"
