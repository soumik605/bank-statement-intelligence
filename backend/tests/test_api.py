# backend/tests/test_api.py
from fastapi.testclient import TestClient

from app.main import app
from app.routes import REPO_ROOT

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_schema():
    r = client.get("/api/v1/schema")
    assert r.status_code == 200
    assert r.json()["title"] == "PipelinePayload"


def test_samples_lists_statements_only():
    r = client.get("/api/v1/samples")
    assert r.status_code == 200
    names = r.json()
    expected = sorted(p.name for p in (REPO_ROOT / "sample_statements").iterdir() if p.suffix in {".csv", ".pdf"})
    assert names == expected
    assert names, "no sample statements found"
    assert not any(n.endswith(".json") for n in names)


def test_every_sample_has_truth_file():
    for name in client.get("/api/v1/samples").json():
        stem = name.rsplit(".", 1)[0]
        assert (REPO_ROOT / "sample_statements" / f"{stem}.truth.json").is_file(), name


def test_samples_dir_env_override(tmp_path, monkeypatch):
    (tmp_path / "demo.pdf").write_bytes(b"%PDF")
    (tmp_path / "notes.txt").write_text("x")
    monkeypatch.setenv("SAMPLES_DIR", str(tmp_path))
    assert client.get("/api/v1/samples").json() == ["demo.pdf"]


def test_samples_dir_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("SAMPLES_DIR", str(tmp_path / "missing"))
    assert client.get("/api/v1/samples").json() == []
