"""Integration tests for the full ingest pipeline (store silo isolation, idempotency, quarantine)."""
import shutil
import tempfile
from pathlib import Path

import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def patch_stores_root(monkeypatch, tmp_path):
    """Redirect store silo writes to a temp directory."""
    from ..persistence import store_silo as silo_mod
    from ..ingest import pipeline as pipe_mod
    monkeypatch.setattr(silo_mod, "STORES_ROOT", tmp_path / "stores")
    monkeypatch.setattr(pipe_mod, "INBOUND_DIR", tmp_path / "inbound")
    monkeypatch.setattr(pipe_mod, "PROCESSED_DIR", tmp_path / "processed")
    monkeypatch.setattr(pipe_mod, "QUARANTINE_DIR", tmp_path / "quarantine")
    (tmp_path / "inbound").mkdir()


def _make_clients_csv(tmp_path, filename="Clients_With_Service.csv") -> Path:
    rows = [
        ["Service Category", "Client Name", "Client Phone", "Service Description", "Date"],
        ["Haircut", "John Doe", "5551234567", "Kids Cut", "04/01/2024"],
        ["Haircut", "Jane Smith", "5559876543", "Trim", "04/02/2024"],
    ]
    path = tmp_path / "inbound" / filename
    pd.DataFrame(rows).to_csv(path, index=False, header=False)
    return path


def test_ingest_creates_store_silo(tmp_path, patch_stores_root):
    from ..ingest.pipeline import ingest_file
    path = _make_clients_csv(tmp_path)
    result = ingest_file(path, store_id="sharkeys_test", store_name="Sharkeys Test")
    assert result["status"] == "ok"
    assert result["rows_loaded"] == 2


def test_ingest_idempotent(tmp_path, patch_stores_root):
    """Reprocessing the same file should be a no-op (duplicate)."""
    from ..ingest.pipeline import ingest_file
    path = _make_clients_csv(tmp_path)
    r1 = ingest_file(path, store_id="sharkeys_test", store_name="Sharkeys Test")
    path2 = _make_clients_csv(tmp_path)
    r2 = ingest_file(path2, store_id="sharkeys_test", store_name="Sharkeys Test")
    assert r1["status"] == "ok"
    assert r2["status"] == "duplicate"


def test_store_silo_isolation(tmp_path, patch_stores_root):
    """Two stores must not share the same database."""
    from ..persistence import store_silo as silo_mod
    silo_mod.STORES_ROOT = tmp_path / "stores"
    silo_mod.create_store_silo("store_a", "Store A")
    silo_mod.create_store_silo("store_b", "Store B")
    db_a = silo_mod.get_store_path("store_a") / "sun_agent.db"
    db_b = silo_mod.get_store_path("store_b") / "sun_agent.db"
    assert db_a.exists()
    assert db_b.exists()
    assert db_a != db_b


def test_quarantine_on_unknown_store(tmp_path, patch_stores_root):
    """A file with no store identity should land in quarantine."""
    from ..ingest.pipeline import ingest_file
    path = tmp_path / "inbound" / "report.csv"
    pd.DataFrame([["A", "B"], ["1", "2"]]).to_csv(path, index=False, header=False)
    result = ingest_file(path)
    assert result["status"] in ("quarantined", "error")
