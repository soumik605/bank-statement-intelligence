import httpx
import pytest
from fastapi.testclient import TestClient

from app import llm
from app.main import app
from scripts.generate_samples import generate

client = TestClient(app)


@pytest.fixture(scope="module")
def csv_sample(tmp_path_factory):
    out = tmp_path_factory.mktemp("samples")
    generate(out)
    return (out / "konark_jan2026.csv").read_bytes()


@pytest.fixture
def calls(monkeypatch):
    """Record which model each LLM call asked for."""
    seen = []

    def chat(messages, **kwargs):
        seen.append(kwargs.get("model"))
        return "[]" if "categorize" in messages[0]["content"] else "Fine."
    monkeypatch.setattr(llm, "chat", chat)
    return seen


def test_models_endpoint():
    body = client.get("/models").json()
    keys = [m["key"] for m in body["models"]]
    assert keys == ["llama-3.2-11b", "nemotron-3-super"]
    assert body["default"] == "llama-3.2-11b"
    nemotron = body["models"][1]
    assert nemotron["id"] == "nvidia/nemotron-3-super-120b-a12b" and nemotron["label"]


@pytest.mark.parametrize("model, model_id", [
    (None, "meta/llama-3.2-11b-vision-instruct"),
    ("nemotron-3-super", "nvidia/nemotron-3-super-120b-a12b"),
])
def test_chosen_model_is_used_for_both_ai_steps_and_recorded(csv_sample, calls, model, model_id):
    data = {"model": model} if model else {}
    body = client.post("/analyze", files={"file": ("k.csv", csv_sample)}, data=data).json()
    expected_key = model or "llama-3.2-11b"
    assert calls and set(calls) == {expected_key}  # categorize and summary
    assert body["ai_model"] == model_id
    assert client.get(f"/history/{body['analysis_id']}").json()["ai_model"] == model_id


def test_unknown_model_is_rejected_before_any_work(csv_sample, calls):
    response = client.post("/analyze", files={"file": ("k.csv", csv_sample)}, data={"model": "gpt-9"})
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "unknown_model"
    assert calls == [] and client.get("/failures").json()["total"] == 0


def test_retry_takes_a_model(csv_sample, calls, monkeypatch):
    def broken(*args, **kwargs):
        raise ValueError("boom")
    import app.main as main
    working = main.analyze_file
    monkeypatch.setattr(main, "analyze_file", broken)
    failure_id = client.post("/analyze", files={"file": ("k.csv", csv_sample)}).json()["detail"]["failure_id"]
    monkeypatch.setattr(main, "analyze_file", working)
    body = client.post(f"/failures/{failure_id}/retry", data={"model": "nemotron-3-super"}).json()
    assert body["ai_model"] == "nvidia/nemotron-3-super-120b-a12b"


def test_custom_default_from_environment(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "nemotron-3-super")
    assert client.get("/models").json()["default"] == "nemotron-3-super"
    monkeypatch.setenv("LLM_MODEL", "some/other-model")
    body = client.get("/models").json()
    assert body["default"] == "custom" and body["models"][0]["id"] == "some/other-model"


def test_chat_sends_model_specific_fields_and_strips_reasoning(monkeypatch):
    sent = {}

    def post(url, *, headers, json, timeout):
        sent.update(json)
        content = "<think>the user wants a number</think>9.8"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]}, request=httpx.Request("POST", url))
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setattr(httpx, "post", post)

    assert llm.chat([{"role": "user", "content": "?"}], model="nemotron-3-super") == "9.8"
    assert sent["model"] == "nvidia/nemotron-3-super-120b-a12b"
    assert sent["chat_template_kwargs"] == {"enable_thinking": False}

    sent.clear()
    llm.chat([{"role": "user", "content": "?"}], model="llama-3.2-11b")
    assert sent["model"] == "meta/llama-3.2-11b-vision-instruct" and "chat_template_kwargs" not in sent


def test_unknown_model_in_chat():
    with pytest.raises(llm.UnknownModelError):
        llm.chat([{"role": "user", "content": "?"}], model="gpt-9")


@pytest.fixture
def no_keys(monkeypatch):
    for name in ("NVIDIA_API_KEY", "NEMOTRON_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


def _sent_key(monkeypatch, model):
    seen = {}

    def post(url, *, headers, json, timeout):
        seen["key"] = headers["Authorization"].removeprefix("Bearer ")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    llm.chat([{"role": "user", "content": "?"}], model=model)
    return seen["key"]


def test_each_model_uses_its_own_key(no_keys):
    no_keys.setenv("NVIDIA_API_KEY", "shared-key")
    no_keys.setenv("NEMOTRON_API_KEY", "nemotron-key")
    assert _sent_key(no_keys, "llama-3.2-11b") == "shared-key"
    assert _sent_key(no_keys, "nemotron-3-super") == "nemotron-key"


def test_nemotron_falls_back_to_the_shared_key(no_keys):
    no_keys.setenv("NVIDIA_API_KEY", "shared-key")
    assert _sent_key(no_keys, "nemotron-3-super") == "shared-key"


def test_models_report_whether_they_have_a_key(no_keys):
    no_keys.setenv("NEMOTRON_API_KEY", "nemotron-key")
    available = {m["key"]: m["available"] for m in client.get("/models").json()["models"]}
    assert available == {"llama-3.2-11b": False, "nemotron-3-super": True}


def test_choosing_a_model_without_a_key_is_a_clear_error(no_keys, csv_sample, calls):
    no_keys.setenv("NEMOTRON_API_KEY", "nemotron-key")
    response = client.post("/analyze", files={"file": ("k.csv", csv_sample)}, data={"model": "llama-3.2-11b"})
    assert response.status_code == 422
    assert response.json()["detail"] == {
        "error": "model_unavailable",
        "message": "Llama 3.2 11B has no API key: set NVIDIA_API_KEY in .env (see .env.example)",
    }
    assert calls == []


def test_missing_key_message_names_both_settings(no_keys):
    with pytest.raises(llm.LLMError, match="set NEMOTRON_API_KEY or NVIDIA_API_KEY"):
        llm.chat([{"role": "user", "content": "?"}], model="nemotron-3-super")
