import json

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app import llm
from app.extract import IncorrectPasswordError, PasswordRequiredError, extract_rows
from app.main import app
from app.normalize import load_statement
from scripts.generate_samples import generate

PASSWORD = "THOM0512"
LOCKED = "sahyadri_feb-mar2026_locked.pdf"
client = TestClient(app)


@pytest.fixture(autouse=True)
def fake_llm(monkeypatch):
    monkeypatch.setattr(llm, "chat", lambda messages, **kw: "[]" if "categorize" in messages[0]["content"] else "Fine.")


@pytest.fixture(scope="module")
def samples(tmp_path_factory):
    out = tmp_path_factory.mktemp("samples")
    generate(out)
    return out


@pytest.fixture(scope="module")
def aes_locked(samples, tmp_path_factory):
    """Real banks mostly use AES; the generator's sample is RC4-128. Cover both."""
    path = tmp_path_factory.mktemp("aes") / "meridian_aes256.pdf"
    writer = PdfWriter(clone_from=samples / "meridian_q1-2026.pdf")
    writer.encrypt(PASSWORD, algorithm="AES-256")
    writer.write(path)
    return path


def test_locked_sample_opens_with_password_and_matches_truth(samples):
    truth = json.loads((samples / "sahyadri_feb-mar2026_locked.truth.json").read_text())
    result = load_statement(samples / LOCKED, truth["password"])
    assert len(result.transactions) == len(truth["rows"])
    assert result.statement.model_dump(mode="json") == truth["statement"]


@pytest.mark.parametrize("which", ["rc4", "aes"])
def test_missing_and_wrong_password(samples, aes_locked, which):
    path = samples / LOCKED if which == "rc4" else aes_locked
    with pytest.raises(PasswordRequiredError):
        extract_rows(path)
    with pytest.raises(IncorrectPasswordError):
        extract_rows(path, "wrong")
    assert extract_rows(path, PASSWORD)


def test_password_ignored_for_unprotected_files(samples):
    assert extract_rows(samples / "meridian_q1-2026.pdf", "anything") == extract_rows(samples / "meridian_q1-2026.pdf")
    assert extract_rows(samples / "konark_jan2026.csv", "anything")


def _post(path, password=None):
    data = {"password": password} if password is not None else {}
    return client.post("/analyze", files={"file": (path.name, path.read_bytes())}, data=data)


def test_api_asks_for_password_before_any_llm_call(samples, monkeypatch):
    monkeypatch.setattr(llm, "chat", lambda *a, **k: pytest.fail("LLM called for a locked file"))
    response = _post(samples / LOCKED)
    assert response.status_code == 422
    assert response.json()["detail"] == {
        "error": "password_required",
        "message": f"'{LOCKED}' is password protected. Enter its password to continue.",
    }


def test_api_wrong_password(samples):
    response = _post(samples / LOCKED, "wrong-guess")
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["error"] == "incorrect_password"
    assert "wrong-guess" not in detail["message"]  # never echo the password back


def test_api_right_password(samples, aes_locked):
    for path in (samples / LOCKED, aes_locked):
        response = _post(path, PASSWORD)
        assert response.status_code == 200, response.text
        assert response.json()["transactions"]


def test_empty_password_counts_as_none(samples):
    assert _post(samples / LOCKED, "").json()["detail"]["error"] == "password_required"


def test_password_is_not_stored(samples):
    from app import db
    analysis_id = _post(samples / LOCKED, PASSWORD).json()["analysis_id"]
    raw = db.DB_PATH.read_bytes()
    assert PASSWORD.encode() not in raw
    assert client.get(f"/history/{analysis_id}").status_code == 200  # reopening needs no password
