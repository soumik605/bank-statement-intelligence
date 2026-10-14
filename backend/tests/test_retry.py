import sqlite3

import pytest
from fastapi.testclient import TestClient

import app.main as app_main
from app import db, llm
from app.extract import ExtractionError
from app.main import app
from scripts.generate_samples import generate

client = TestClient(app)


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    monkeypatch.setattr(llm, "chat", lambda messages, **kw: "[]" if "categorize" in messages[0]["content"] else "Fine.")


@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    out = tmp_path_factory.mktemp("samples")
    generate(out)
    return out


def _fail_upload(monkeypatch, name, content, password=None):
    """Upload while the parser is 'broken', so a real file ends up as a failure."""
    def broken(*args, **kwargs):
        raise ExtractionError("no header row found")
    working = app_main.analyze_file
    monkeypatch.setattr(app_main, "analyze_file", broken)
    data = {"password": password} if password else {}
    failure_id = client.post("/analyze", files={"file": (name, content)}, data=data).json()["detail"]["failure_id"]
    # "The parser has been fixed." Restore just this; monkeypatch.undo() would also
    # undo the temporary-database fixture and point the test at the real database.
    monkeypatch.setattr(app_main, "analyze_file", working)
    return failure_id


def _retry(failure_id, password=None):
    return client.post(f"/failures/{failure_id}/retry", data={"password": password} if password else {})


def test_retry_after_a_fix_resolves_the_failure(monkeypatch, samples):
    failure_id = _fail_upload(monkeypatch, "konark.csv", (samples / "konark_jan2026.csv").read_bytes())
    response = _retry(failure_id)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["source_file"] == "konark.csv" and result["transactions"]

    failure = client.get(f"/failures/{failure_id}").json()
    assert failure["resolved_analysis_id"] == result["analysis_id"]
    assert failure["resolved_at"] and failure["retry_count"] == 1
    assert client.get("/history").json()["total"] == 1


def test_retry_that_fails_again_updates_the_same_record():
    failure_id = client.post("/analyze", files={"file": ("junk.csv", b"a,b\n1,2\n")}).json()["detail"]["failure_id"]
    for attempt in (1, 2):
        response = _retry(failure_id)
        assert response.status_code == 422
        assert response.json()["detail"]["failure_id"] == failure_id
        failure = client.get(f"/failures/{failure_id}").json()
        assert failure["retry_count"] == attempt and failure["last_retry_at"] and not failure["resolved_at"]
    assert client.get("/failures").json()["total"] == 1  # no duplicates


def test_retry_records_the_latest_error(monkeypatch, samples):
    failure_id = _fail_upload(monkeypatch, "konark.csv", (samples / "konark_jan2026.csv").read_bytes())

    def crash(*args, **kwargs):
        raise RuntimeError("new bug")
    monkeypatch.setattr("app.main.analyze_file", crash)
    assert _retry(failure_id).status_code == 500
    failure = client.get(f"/failures/{failure_id}").json()
    assert (failure["error_code"], failure["exception_type"]) == ("internal_error", "RuntimeError")
    assert "new bug" in failure["traceback"]


def test_retry_protected_pdf_asks_for_the_password(monkeypatch, samples):
    content = (samples / "sahyadri_feb-mar2026_locked.pdf").read_bytes()
    failure_id = _fail_upload(monkeypatch, "locked.pdf", content, password="THOM0512")
    assert client.get(f"/failures/{failure_id}").json()["password_supplied"] is True

    assert _retry(failure_id).json()["detail"]["error"] == "password_required"
    assert _retry(failure_id, "wrong").json()["detail"]["error"] == "incorrect_password"
    assert client.get(f"/failures/{failure_id}").json()["retry_count"] == 0  # password prompts aren't attempts

    assert _retry(failure_id, "THOM0512").status_code == 200
    assert client.get(f"/failures/{failure_id}").json()["resolved_at"]


def test_cannot_retry_without_a_kept_file():
    failure_id = client.post("/analyze", files={"file": ("empty.csv", b"")}).json()["detail"]["failure_id"]
    response = _retry(failure_id)
    assert response.status_code == 404 and response.json()["detail"]["error"] == "file_not_found"
    assert _retry(9999).json()["detail"]["error"] == "failure_not_found"


def test_deleting_the_resulting_analysis_keeps_the_failure_and_its_file(monkeypatch, samples):
    failure_id = _fail_upload(monkeypatch, "konark.csv", (samples / "konark_jan2026.csv").read_bytes())
    analysis_id = _retry(failure_id).json()["analysis_id"]
    client.delete(f"/history/{analysis_id}")
    failure = client.get(f"/failures/{failure_id}").json()
    assert failure["resolved_at"] and failure["resolved_analysis_id"] is None
    assert failure["file_kept"]  # still referenced by the failure


def test_old_database_is_migrated():
    db.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db.DB_PATH) as conn:  # the failures table as first released
        conn.execute("""CREATE TABLE failures (id INTEGER PRIMARY KEY AUTOINCREMENT, failed_at TEXT NOT NULL,
            source_file TEXT NOT NULL, file_sha256 TEXT, file_size INTEGER NOT NULL, http_status INTEGER NOT NULL,
            error_code TEXT NOT NULL, message TEXT NOT NULL, exception_type TEXT, traceback TEXT,
            password_supplied INTEGER NOT NULL DEFAULT 0)""")
        conn.execute("""INSERT INTO failures (failed_at, source_file, file_size, http_status, error_code, message)
            VALUES ('2026-10-14T05:00:00+00:00', 'old.csv', 0, 400, 'empty_file', 'empty')""")
    failure = client.get("/failures").json()["items"][0]
    assert (failure["source_file"], failure["retry_count"], failure["resolved_at"]) == ("old.csv", 0, None)
