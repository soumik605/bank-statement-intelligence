import json

import pytest
from fastapi.testclient import TestClient

from app import llm
from app.main import app
from scripts.generate_samples import generate

client = TestClient(app)


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    """No network in tests: categorization gets an empty answer (everything left
    becomes Other); the summary gets a reply with no numbers."""
    def chat(messages, **kwargs):
        return "[]" if "categorize" in messages[0]["content"] else "A quiet month with nothing surprising."
    monkeypatch.setattr(llm, "chat", chat)


@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    out = tmp_path_factory.mktemp("samples")
    generate(out)
    return out


def _post(name, content, content_type="application/octet-stream"):
    return client.post("/analyze", files={"file": (name, content, content_type)})


@pytest.mark.parametrize("stem", ["northwind_jan2026", "konark_jan2026", "meridian_q1-2026", "sahyadri_feb-mar2026"])
def test_analyze_sample(samples, stem):
    truth = json.loads((samples / f"{stem}.truth.json").read_text())
    path = samples / truth["source_file"]
    response = _post(path.name, path.read_bytes())
    assert response.status_code == 200, response.text
    body = response.json()

    assert set(body) == {"analysis_id", "analyzed_at", "source_file", "account", "transactions", "summary",
                         "unusual", "ai_summary", "skipped_rows"}
    assert isinstance(body["analysis_id"], int)
    assert body["source_file"] == path.name
    assert body["account"] == truth["statement"]
    assert len(body["transactions"]) == body["summary"]["transaction_count"] == len(truth["rows"])
    assert all(t["category"] and t["category_source"] in ("rule", "llm") for t in body["transactions"])
    assert body["unusual"] == [t for t in body["transactions"] if t["is_unusual"]]
    assert body["ai_summary"] == {"text": "A quiet month with nothing surprising.", "source": "llm", "unsupported_numbers": []}


def test_cors_preflight_from_nextjs():
    response = client.options("/analyze", headers={
        "Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST",
    })
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_cors_rejects_unknown_origin():
    response = client.options("/analyze", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "POST",
    })
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("name, content, status, error", [
    ("notes.txt", b"hello", 415, "unsupported_file_type"),
    ("empty.csv", b"", 400, "empty_file"),
    ("junk.csv", b"a,b\n1,2\n", 422, "could_not_parse"),
    ("fake.pdf", b"not really a pdf", 422, "could_not_parse"),
    ("dates.csv", b"Date,Narration,Debit,Credit\n13/45/2026,X,1.00,\n", 422, "could_not_parse"),
])
def test_clear_errors(name, content, status, error):
    response = _post(name, content)
    assert response.status_code == status
    detail = response.json()["detail"]
    assert detail["error"] == error
    assert name in detail["message"]


def test_parse_error_says_why():
    detail = _post("junk.csv", b"a,b\n1,2\n").json()["detail"]
    assert "no header row found" in detail["message"]


def test_too_large(monkeypatch):
    monkeypatch.setattr("app.main.MAX_UPLOAD_BYTES", 10)
    assert _post("big.csv", b"x" * 11).status_code == 413


def test_docs_page():
    assert client.get("/docs").status_code == 200
    assert "/analyze" in client.get("/openapi.json").json()["paths"]


def test_samples_are_served():
    names = client.get("/samples").json()
    assert "meridian_q1-2026.pdf" in names and not any(n.endswith(".json") for n in names)
    response = client.get("/samples/meridian_q1-2026.pdf")
    assert response.status_code == 200 and response.content.startswith(b"%PDF")


@pytest.mark.parametrize("name", ["meridian_q1-2026.truth.json", "..%2F..%2F.env", "nope.pdf"])
def test_only_listed_samples_are_served(name):
    assert client.get(f"/samples/{name}").status_code == 404
