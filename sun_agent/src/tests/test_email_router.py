"""Tests for store routing from email headers."""
import json
import pytest
from pathlib import Path


@pytest.fixture(autouse=True)
def patch_routing_file(tmp_path, monkeypatch):
    routing = {
        "name_patterns": {
            "grand rapids": "sharkeys_grand_rapids",
            "dallas": "sharkeys_dallas",
        },
        "reply_to_patterns": {
            "grandrapids@sharkeyscutsforkids.com": "sharkeys_grand_rapids",
        },
        "store_names": {
            "sharkeys_grand_rapids": "Sharkey's Cuts for Kids - Grand Rapids",
            "sharkeys_dallas": "Sharkey's Cuts for Kids - Dallas",
        },
    }
    routing_path = tmp_path / "store_routing.json"
    routing_path.write_text(json.dumps(routing))

    from ..ingest import email_router as router_mod
    monkeypatch.setattr(router_mod, "ROUTING_FILE", routing_path)


def test_resolve_by_display_name():
    from ..ingest.email_router import resolve_store_from_email
    store_id, store_name = resolve_store_from_email(
        "Sharkey's Cuts for Kids - Grand Rapids",
        "grandrapids@sharkeyscutsforkids.com",
    )
    assert store_id == "sharkeys_grand_rapids"
    assert "Grand Rapids" in store_name


def test_resolve_by_reply_to_fallback():
    from ..ingest.email_router import resolve_store_from_email
    store_id, store_name = resolve_store_from_email(
        "Sharkey's Cuts",
        "grandrapids@sharkeyscutsforkids.com",
    )
    assert store_id == "sharkeys_grand_rapids"


def test_resolve_case_insensitive():
    from ..ingest.email_router import resolve_store_from_email
    store_id, _ = resolve_store_from_email(
        "SHARKEY'S CUTS FOR KIDS - GRAND RAPIDS", ""
    )
    assert store_id == "sharkeys_grand_rapids"


def test_resolve_unknown_store_derives_id():
    from ..ingest.email_router import resolve_store_from_email
    store_id, store_name = resolve_store_from_email(
        "Sharkey's Cuts for Kids - Phoenix", ""
    )
    assert store_id is not None
    assert "phoenix" in store_id
    assert store_name == "Sharkey's Cuts for Kids - Phoenix"


def test_resolve_empty_returns_none():
    from ..ingest.email_router import resolve_store_from_email
    store_id, store_name = resolve_store_from_email("", "")
    assert store_id is None


def test_decode_from_header():
    """Ensure display name is extracted correctly from a real From header."""
    raw = '"Sharkey\'s Cuts for Kids - Grand Rapids" <mailer@salonultimate.email>'
    display_name = raw.split("<")[0].strip().strip('"')
    assert display_name == "Sharkey's Cuts for Kids - Grand Rapids"
