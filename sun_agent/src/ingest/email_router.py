"""
Resolves store_id from Salon Ultimate email headers.
Primary signal: From display name (e.g. "Sharkey's Cuts for Kids - Grand Rapids")
Fallback: reply-to email address
"""
import json
import re
from pathlib import Path

ROUTING_FILE = Path(__file__).resolve().parents[3] / "store_routing.json"


def _load_routing() -> dict:
    if ROUTING_FILE.exists():
        return json.loads(ROUTING_FILE.read_text())
    return {"name_patterns": {}, "reply_to_patterns": {}, "store_names": {}}


def resolve_store_from_email(from_display_name: str, reply_to: str = "") -> tuple:
    """
    Returns (store_id, store_name) or (None, None) if unresolvable.
    from_display_name: e.g. "Sharkey's Cuts for Kids - Grand Rapids"
    reply_to: e.g. "grandrapids@sharkeyscutsforkids.com"
    """
    routing = _load_routing()
    name_lower = from_display_name.lower()
    reply_lower = (reply_to or "").lower()

    # Primary: match against From display name substrings
    for pattern, store_id in routing.get("name_patterns", {}).items():
        if pattern.lower() in name_lower:
            store_name = routing.get("store_names", {}).get(store_id, from_display_name)
            return store_id, store_name

    # Fallback: match reply-to address
    for pattern, store_id in routing.get("reply_to_patterns", {}).items():
        if pattern.lower() in reply_lower:
            store_name = routing.get("store_names", {}).get(store_id, from_display_name)
            return store_id, store_name

    # Last resort: derive store_id from display name directly
    if from_display_name.strip():
        derived = re.sub(r"[^a-z0-9]+", "_", name_lower).strip("_")
        return derived, from_display_name.strip()

    return None, None


def add_store_mapping(pattern: str, store_id: str, store_name: str,
                      pattern_type: str = "name"):
    """Add a new store routing entry and persist it."""
    routing = _load_routing()
    if pattern_type == "name":
        routing.setdefault("name_patterns", {})[pattern.lower()] = store_id
    else:
        routing.setdefault("reply_to_patterns", {})[pattern.lower()] = store_id
    routing.setdefault("store_names", {})[store_id] = store_name
    ROUTING_FILE.write_text(json.dumps(routing, indent=2))
