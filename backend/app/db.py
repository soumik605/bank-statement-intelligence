"""Local history database (SQLite): every analysis is kept so it can be listed,
reopened without re-running the AI, previewed, or deleted; failed uploads are
kept too, so the parser can be improved against real files.

- analyses: one row per successful run. Holds the headline figures for
  listing, plus the exact JSON that was returned, so reopening shows precisely
  what was seen then.
- transactions: one row per transaction of each run, for querying across
  statements (e.g. food spending per month).
- failures: one row per failed upload, with the error and traceback.

Uploaded files are stored next to the database in uploads/, named by their
SHA-256, so the same file uploaded twice is stored once. A protected PDF is
stored as uploaded, still encrypted; its password is never stored.

The database lives at data/history.db, or wherever DATABASE_PATH points.
"""
import datetime as dt
import hashlib
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.schema import AnalyzeResponse, FailureItem, FailurePage, HistoryItem, HistoryPage

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

CREATE TABLE IF NOT EXISTS failures (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    failed_at         TEXT    NOT NULL,              -- ISO 8601, UTC
    source_file       TEXT    NOT NULL,
    file_sha256       TEXT,                          -- null when no file was kept (empty or too large)
    file_size         INTEGER NOT NULL,
    http_status       INTEGER NOT NULL,
    error_code        TEXT    NOT NULL,              -- e.g. could_not_parse, internal_error
    message           TEXT    NOT NULL,              -- what the user was shown
    exception_type    TEXT,
    traceback         TEXT,
    password_supplied INTEGER NOT NULL DEFAULT 0     -- a protected PDF; reproducing needs its password
);
CREATE INDEX IF NOT EXISTS failures_failed_at ON failures (failed_at);
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


# --- Stored files ----------------------------------------------------------------

def uploads_dir() -> Path:
    return DB_PATH.parent / "uploads"


def _store_file(content: bytes) -> str:
    """Keep the uploaded bytes, named by SHA-256; returns the hash."""
    sha = hashlib.sha256(content).hexdigest()
    path = uploads_dir() / sha
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{sha}.tmp")
        tmp.write_bytes(content)
        tmp.replace(path)  # atomic: a reader never sees half a file
    return sha


def stored_file(sha: str | None) -> Path | None:
    path = uploads_dir() / sha if sha else None
    return path if path and path.is_file() else None


def _remove_file_if_unused(conn: sqlite3.Connection, sha: str | None) -> None:
    if not sha:
        return
    used = conn.execute(
        "SELECT (SELECT COUNT(*) FROM analyses WHERE file_sha256 = ?) + (SELECT COUNT(*) FROM failures WHERE file_sha256 = ?)",
        (sha, sha),
    ).fetchone()[0]
    if not used:
        (uploads_dir() / sha).unlink(missing_ok=True)


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC).replace(microsecond=0)


# --- Analyses ---------------------------------------------------------------------

def save_analysis(result: AnalyzeResponse, content: bytes) -> AnalyzeResponse:
    """Store one run and its file; return the result with analysis_id and analyzed_at filled in."""
    analyzed_at = _now()
    s, a = result.summary, result.account
    _store_file(content)
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


def get_analysis_file(analysis_id: int) -> tuple[Path, str] | None:
    """(stored file, original name), or None if the run or its file is gone. Runs
    saved before files were kept have no file."""
    with connect() as conn:
        row = conn.execute("SELECT file_sha256, source_file FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
    path = stored_file(row["file_sha256"]) if row else None
    return (path, row["source_file"]) if path else None


def delete_analysis(analysis_id: int) -> bool:
    """Delete one run, its transactions, and its file unless something else uses it."""
    with connect() as conn:
        row = conn.execute("SELECT file_sha256 FROM analyses WHERE id = ?", (analysis_id,)).fetchone()
        if row is None:
            return False
        conn.execute("DELETE FROM analyses WHERE id = ?", (analysis_id,))
        _remove_file_if_unused(conn, row["file_sha256"])
    return True


# --- Failures ---------------------------------------------------------------------

def save_failure(
    *,
    source_file: str,
    content: bytes | None,
    http_status: int,
    error_code: str,
    message: str,
    exception: BaseException | None = None,
    traceback: str | None = None,
    password_supplied: bool = False,
) -> int:
    """Record a failed upload, keeping the file when there is one. Returns its id."""
    sha = _store_file(content) if content else None
    with connect() as conn:
        cursor = conn.execute(
            """INSERT INTO failures (failed_at, source_file, file_sha256, file_size, http_status, error_code,
                   message, exception_type, traceback, password_supplied)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                _now().isoformat(), source_file, sha, len(content or b""), http_status, error_code, message,
                type(exception).__qualname__ if exception else None, traceback, int(password_supplied),
            ),
        )
        return cursor.lastrowid


def _failure(row: sqlite3.Row) -> FailureItem:
    data = dict(row)
    return FailureItem(**data, file_kept=stored_file(data["file_sha256"]) is not None)


def list_failures(limit: int = 20, offset: int = 0) -> FailurePage:
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM failures").fetchone()[0]
        rows = conn.execute(
            "SELECT * FROM failures ORDER BY failed_at DESC, id DESC LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
    return FailurePage(items=[_failure(r) for r in rows], total=total)


def get_failure(failure_id: int) -> FailureItem | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM failures WHERE id = ?", (failure_id,)).fetchone()
    return _failure(row) if row else None


def get_failure_file(failure_id: int) -> tuple[Path, str] | None:
    failure = get_failure(failure_id)
    path = stored_file(failure.file_sha256) if failure else None
    return (path, failure.source_file) if path else None


def delete_failure(failure_id: int) -> bool:
    with connect() as conn:
        row = conn.execute("SELECT file_sha256 FROM failures WHERE id = ?", (failure_id,)).fetchone()
        if row is None:
            return False
        conn.execute("DELETE FROM failures WHERE id = ?", (failure_id,))
        _remove_file_if_unused(conn, row["file_sha256"])
    return True
