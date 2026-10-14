import os
import tempfile
from pathlib import Path

# Before anything imports app.db: its default DATABASE_PATH must never be the real
# data/history.db during tests, even if a test's monkeypatching gets undone.
os.environ["DATABASE_PATH"] = str(Path(tempfile.mkdtemp(prefix="bsi-tests-")) / "history.db")

import pytest  # noqa: E402

from app import db  # noqa: E402

REAL_DATABASE = Path(__file__).resolve().parents[2] / "data" / "history.db"


@pytest.fixture(autouse=True)
def temp_database(tmp_path, monkeypatch):
    """Every test gets its own empty history database, never the real one."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "history.db")
    yield
    assert db.DB_PATH.resolve() != REAL_DATABASE.resolve(), "a test pointed at the real history database"
