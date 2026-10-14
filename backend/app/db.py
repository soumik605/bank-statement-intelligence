"""Local history database (SQLite): every analysis is kept so it can be listed,
reopened without re-running the AI, or deleted.

- analyses: one row per run. Holds the headline figures for listing, plus the
  exact JSON that was returned, so reopening shows precisely what was seen then.
- transactions: one row per transaction of each run, for querying across
  statements (e.g. food spending per month).

The uploaded file itself is not stored, only its name, size and SHA-256.

The database lives at data/history.db, or wherever DATABASE_PATH points.
"""
import datetime as dt
import hashlib
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.schema import AnalyzeResponse, HistoryItem, HistoryPage

DB_PATH = Path(os.environ.get("DATABASE_PATH") or Path(__file__).resolve().parents[2] / "data" / "history.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    analyzed_at       TEXT    NOT NULL,              -- ISO 8601, UTC
    source_file       TEXT    NOT NULL,
    file_sha256       TEXT    NOT NULL,
    file_size         INTEGER NOT NULL,
    bank_name         TEXT,
    account_number    TEXT,                          -- masked, XXXX1234
    holder_name       TEXT,
    period_start      TEXT    NOT NULL,
    period_end        TEXT    NOT NULL,
    transaction_count INTEGER NOT NULL,
    total_credits     REAL    NOT NULL,
    total_debits      REAL    NOT NULL,
    net_flow          REAL    NOT NULL,
    total_spending    REAL    NOT NULL,
    unusual_count     INTEGER NOT NULL,
    ai_summary_source TEXT    NOT NULL CHECK (ai_summary_source IN ('llm', 'template')),
    result_json       TEXT    NOT NULL               -- the AnalyzeResponse exactly as returned
);
CREATE INDEX IF NOT EXISTS analyses_analyzed_at ON analyses (analyzed_at);
CREATE INDEX IF NOT EXISTS analyses_file_sha256 ON analyses (file_sha256);

CREATE TABLE IF NOT EXISTS transactions (
    analysis_id     INTEGER NOT NULL REFERENCES analyses (id) ON DELETE CASCADE,
    position        INTEGER NOT NULL,                -- order in the statement, from 0
    date            TEXT    NOT NULL,
    description     TEXT    NOT NULL,
    amount          REAL    NOT NULL CHECK (amount > 0),
    type            TEXT    NOT NULL CHECK (type IN ('credit', 'debit')),
    balance         REAL,
    category        TEXT,
    category_source TEXT CHECK (category_source IN ('rule', 'llm')),
    is_unusual      INTEGER NOT NULL,
    unusual_reason  TEXT,
    PRIMARY KEY (analysis_id, position)
);
CREATE INDEX IF NOT EXISTS transactions_date ON transactions (date);
"""


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """A connection per call: FastAPI runs sync endpoints on worker threads, and
    SQLite connections can't be shared across threads. Commits on success."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")  # off by default in SQLite; needed for the cascade
        conn.execute("PRAGMA journal_mode = WAL")  # readers don't block the writer
        conn.executescript(SCHEMA)
        with conn:
            yield conn
    finally:
        conn.close()


def save_analysis(result: AnalyzeResponse, content: bytes) -> AnalyzeResponse:
    """Store one run and return the result with its analysis_id and analyzed_at filled in."""
    analyzed_at = dt.datetime.now(dt.UTC).replace(microsecond=0)
    s, a = result.summary, result.account
    with connect() as conn:
        cursor = conn.execute(
            """INSERT INTO analyses (analyzed_at, source_file, file_sha256, file_size, bank_name, account_number,
                   holder_name, period_start, period_end, transaction_count, total_credits, total_debits, net_flow,
                   total_spending, unusual_count, ai_summary_source, result_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '')""",
            (
                analyzed_at.isoformat(), result.source_file, hashlib.sha256(content).hexdigest(), len(content),
                a.bank_name, a.account_number, a.holder_name, a.period_start.isoformat(), a.period_end.isoformat(),
                s.transaction_count, s.total_credits, s.total_debits, s.net_flow, s.total_spending,
                len(result.unusual), result.ai_summary.source,
            ),
        )
        saved = result.model_copy(update={"analysis_id": cursor.lastrowid, "analyzed_at": analyzed_at})
        conn.execute("UPDATE analyses SET result_json = ? WHERE id = ?", (saved.model_dump_json(), saved.analysis_id))
        conn.executemany(
            """INSERT INTO transactions (analysis_id, position, date, description, amount, type, balance,
                   category, category_source, is_unusual, unusual_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (saved.analysis_id, i, t.date.isoformat(), t.description, t.amount, t.type, t.balance,
                 t.category.value if t.category else None, t.category_source, int(t.is_unusual), t.unusual_reason)
                for i, t in enumerate(result.transactions)
            ],
        )
    return saved


def list_analyses(limit: int = 20, offset: int = 0) -> HistoryPage:
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM analyses").fetchone()[0]
        rows = conn.execute(
            """SELECT id, analyzed_at, source_file, file_sha256, bank_name, account_number, period_start, period_end,
                      transaction_count, total_credits, total_debits, net_flow, unusual_count, ai_summary_source
               FROM analyses ORDER BY analyzed_at DESC, id DESC LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
    return HistoryPage(items=[HistoryItem(**dict(row)) for row in rows], total=total)


def get_analysis(analysis_id: int) -> AnalyzeResponse | None:
    with connect() as conn:
        row = conn.execute("SELECT result_json FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
    return AnalyzeResponse.model_validate_json(row["result_json"]) if row else None


def delete_analysis(analysis_id: int) -> bool:
    """Delete one run and its transactions. False if it didn't exist."""
    with connect() as conn:
        return conn.execute("DELETE FROM analyses WHERE id = ?", (analysis_id,)).rowcount > 0
