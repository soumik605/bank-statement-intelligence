import pytest
from fastapi.testclient import TestClient

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


def _post(name, content, password=None):
    data = {"password": password} if password else {}
    return client.post("/analyze", files={"file": (name, content)}, data=data)


def _stored_files():
    return sorted(p.name for p in db.uploads_dir().glob("*")) if db.uploads_dir().exists() else []


# --- Preview of the original document ------------------------------------------------

@pytest.mark.parametrize("name, media_type", [("meridian_q1-2026.pdf", "application/pdf"), ("konark_jan2026.csv", "text/csv")])
def test_original_file_is_served_inline(samples, name, media_type):
    content = (samples / name).read_bytes()
    analysis_id = _post(name, content).json()["analysis_id"]
    response = client.get(f"/history/{analysis_id}/file")
    assert response.status_code == 200
    assert response.content == content
    assert response.headers["content-type"].startswith(media_type)
    assert response.headers["content-disposition"].startswith("inline")


def test_protected_pdf_is_stored_still_encrypted(samples):
    content = (samples / "sahyadri_feb-mar2026_locked.pdf").read_bytes()
    analysis_id = _post("locked.pdf", content, "THOM0512").json()["analysis_id"]
    assert client.get(f"/history/{analysis_id}/file").content == content  # as uploaded, not decrypted


def test_same_file_is_stored_once_and_removed_with_its_last_record(samples):
    content = (samples / "konark_jan2026.csv").read_bytes()
    a = _post("a.csv", content).json()["analysis_id"]
    b = _post("b.csv", content).json()["analysis_id"]
    assert len(_stored_files()) == 1
    client.delete(f"/history/{a}")
    assert client.get(f"/history/{b}/file").status_code == 200  # still used by b
    client.delete(f"/history/{b}")
    assert _stored_files() == []


def test_missing_file_is_a_clear_404(samples):
    analysis_id = _post("k.csv", (samples / "konark_jan2026.csv").read_bytes()).json()["analysis_id"]
    for path in db.uploads_dir().glob("*"):
        path.unlink()  # like a record saved before files were kept
    response = client.get(f"/history/{analysis_id}/file")
    assert response.status_code == 404 and response.json()["detail"]["error"] == "file_not_found"


# --- Failed uploads are kept --------------------------------------------------------------

def test_parse_failure_is_recorded_with_file_and_traceback():
    content = b"a,b\n1,2\n"
    detail = _post("junk.csv", content).json()["detail"]
    failure = client.get(f"/failures/{detail['failure_id']}").json()
    assert failure["error_code"] == "could_not_parse"
    assert failure["message"] == detail["message"]
    assert failure["exception_type"] == "ExtractionError"
    assert "no header row found" in failure["traceback"]
    assert failure["file_kept"] and failure["file_size"] == len(content)
    assert client.get(f"/failures/{failure['id']}/file").content == content


def test_corrupt_pdf_is_recorded():
    detail = _post("broken.pdf", b"not really a pdf").json()["detail"]
    failure = client.get(f"/failures/{detail['failure_id']}").json()
    assert (failure["error_code"], failure["exception_type"]) == ("could_not_parse", "PdfminerException")


def test_internal_error_is_recorded(monkeypatch, samples):
    def boom(*args, **kwargs):
        raise RuntimeError("something unexpected")
    monkeypatch.setattr("app.main.analyze_file", boom)
    response = _post("k.csv", (samples / "konark_jan2026.csv").read_bytes())
    assert response.status_code == 500
    failure = client.get(f"/failures/{response.json()['detail']['failure_id']}").json()
    assert failure["exception_type"] == "RuntimeError" and "something unexpected" in failure["traceback"]


@pytest.mark.parametrize("name, content, code, kept", [
    ("notes.txt", b"hello", "unsupported_file_type", True),
    ("empty.csv", b"", "empty_file", False),
])
def test_rejected_uploads_are_recorded(name, content, code, kept):
    failure_id = _post(name, content).json()["detail"]["failure_id"]
    failure = client.get(f"/failures/{failure_id}").json()
    assert (failure["error_code"], failure["file_kept"]) == (code, kept)


def test_too_large_is_recorded_without_the_file(monkeypatch):
    monkeypatch.setattr("app.main.MAX_UPLOAD_BYTES", 10)
    failure_id = _post("big.csv", b"x" * 11).json()["detail"]["failure_id"]
    assert client.get(f"/failures/{failure_id}").json()["file_kept"] is False


def test_password_prompts_are_not_failures(samples):
    content = (samples / "sahyadri_feb-mar2026_locked.pdf").read_bytes()
    assert "failure_id" not in _post("locked.pdf", content).json()["detail"]
    assert "failure_id" not in _post("locked.pdf", content, "wrong").json()["detail"]
    assert client.get("/failures").json()["total"] == 0


def test_failure_after_password_is_flagged(monkeypatch):
    def fails(*args, **kwargs):
        raise ExtractionError("no header row found")
    monkeypatch.setattr("app.main.analyze_file", fails)
    failure_id = _post("x.pdf", b"%PDF-locked-bytes", "secret").json()["detail"]["failure_id"]
    failure = client.get(f"/failures/{failure_id}").json()
    assert failure["password_supplied"] is True
    assert "secret" not in str(failure)


def test_list_and_delete_failures():
    ids = [_post(f"junk{i}.csv", f"a,b\n{i},2\n".encode()).json()["detail"]["failure_id"] for i in range(3)]
    page = client.get("/failures", params={"limit": 2}).json()
    assert page["total"] == 3 and [f["id"] for f in page["items"]] == [ids[2], ids[1]]
    assert client.delete(f"/failures/{ids[0]}").status_code == 204
    assert client.get(f"/failures/{ids[0]}").status_code == 404
    assert len(_stored_files()) == 2  # its file went with it


def test_recording_failure_never_hides_the_real_error(monkeypatch):
    def broken(**kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(db, "save_failure", broken)
    response = _post("junk.csv", b"a,b\n1,2\n")
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "could_not_parse" and "failure_id" not in response.json()["detail"]
