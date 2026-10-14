import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import db, llm
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


def _upload(samples, name):
    path = samples / name
    response = client.post("/analyze", files={"file": (name, path.read_bytes())})
    assert response.status_code == 200, response.text
    return response.json()


def test_analysis_is_saved_and_listed(samples):
    first = _upload(samples, "konark_jan2026.csv")
    second = _upload(samples, "meridian_q1-2026.pdf")

    page = client.get("/history").json()
    assert page["total"] == 2
    assert [item["id"] for item in page["items"]] == [second["analysis_id"], first["analysis_id"]]  # newest first
    item = page["items"][1]
    assert item["source_file"] == "konark_jan2026.csv"
    assert item["account_number"] == "XXXX7710"
    assert item["transaction_count"] == first["summary"]["transaction_count"]
    assert item["unusual_count"] == len(first["unusual"])


def test_reopen_returns_exactly_what_was_returned(samples, monkeypatch):
    original = _upload(samples, "meridian_q1-2026.pdf")
    # Reopening must not call the LLM again.
    monkeypatch.setattr(llm, "chat", lambda *a, **k: pytest.fail("LLM called on reopen"))
    assert client.get(f"/history/{original['analysis_id']}").json() == original


def test_same_file_twice_is_two_records_with_one_fingerprint(samples):
    a = _upload(samples, "konark_jan2026.csv")
    b = _upload(samples, "konark_jan2026.csv")
    assert a["analysis_id"] != b["analysis_id"]
    items = client.get("/history").json()["items"]
    assert items[0]["file_sha256"] == items[1]["file_sha256"]


def test_transactions_are_queryable(samples):
    result = _upload(samples, "northwind_jan2026.csv")
    with sqlite3.connect(db.DB_PATH) as conn:
        rows = conn.execute(
            "SELECT category, SUM(amount) FROM transactions WHERE analysis_id = ? AND type = 'debit' GROUP BY category",
            (result["analysis_id"],),
        ).fetchall()
    assert dict(rows)["Food"] > 0


def test_delete_removes_record_and_its_transactions(samples):
    result = _upload(samples, "konark_jan2026.csv")
    assert client.delete(f"/history/{result['analysis_id']}").status_code == 204
    assert client.get(f"/history/{result['analysis_id']}").status_code == 404
    assert client.delete(f"/history/{result['analysis_id']}").status_code == 404
    with sqlite3.connect(db.DB_PATH) as conn:
        assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0


def test_pagination(samples):
    ids = [_upload(samples, "konark_jan2026.csv")["analysis_id"] for _ in range(3)]
    page = client.get("/history", params={"limit": 2, "offset": 1}).json()
    assert page["total"] == 3
    assert [i["id"] for i in page["items"]] == [ids[1], ids[0]]


def test_failed_parse_is_not_saved():
    client.post("/analyze", files={"file": ("junk.csv", b"a,b\n1,2\n")})
    assert client.get("/history").json()["total"] == 0


def test_save_failure_still_returns_the_analysis(samples, monkeypatch):
    def broken(*args):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(db, "save_analysis", broken)
    result = _upload(samples, "konark_jan2026.csv")
    assert result["analysis_id"] is None and result["transactions"]


def test_unknown_id():
    response = client.get("/history/999")
    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "analysis_not_found"
