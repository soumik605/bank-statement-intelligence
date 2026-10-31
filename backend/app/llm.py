"""Minimal client for an OpenAI-compatible chat completions API, with a fixed
set of models the app may use.

Defaults to NVIDIA's hosted endpoint. Settings come from the environment, or
from the repo-root .env (see .env.example):

    NVIDIA_API_KEY   key for every model, unless the model has its own below
    NEMOTRON_API_KEY optional: a separate key for Nemotron (falls back to NVIDIA_API_KEY)
    LLM_BASE_URL     default https://integrate.api.nvidia.com/v1
    LLM_MODEL        which of MODELS is the default (its key or model id);
                     any other model id also works, as a custom default
"""
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"


@dataclass(frozen=True)
class Model:
    key: str  # what the API and the UI use to pick it
    id: str  # the provider's model id
    label: str
    note: str
    extra: dict = field(default_factory=dict)  # model-specific request fields
    api_key_env: str = "NVIDIA_API_KEY"  # where this model's key lives; NVIDIA_API_KEY is the fallback


# The models a user can choose. The first is the default unless LLM_MODEL says otherwise.
MODELS: dict[str, Model] = {
    m.key: m
    for m in [
        Model(
            "llama-3.2-11b",
            "meta/llama-3.2-11b-vision-instruct",
            "Llama 3.2 11B",
            "Fast, small model",
        ),
        Model(
            "nemotron-3-super",
            "nvidia/nemotron-3-super-120b-a12b",
            "Nemotron 3 Super 120B",
            "Larger model, usually more accurate",
            # A reasoning model: thinking spends output tokens and time before the answer.
            # Sorting transactions into categories and wording given figures don't need it.
            {"chat_template_kwargs": {"enable_thinking": False}},
            api_key_env="NEMOTRON_API_KEY",
        ),
    ]
}


class LLMError(RuntimeError):
    pass


class UnknownModelError(ValueError):
    pass


def default_model() -> Model:
    configured = os.environ.get("LLM_MODEL", "").strip()
    if not configured:
        return next(iter(MODELS.values()))
    for model in MODELS.values():
        if configured in (model.key, model.id):
            return model
    return Model("custom", configured, configured, "Set by LLM_MODEL")


def resolve_model(key: str | None) -> Model:
    """The model for a key from MODELS; None means the default."""
    if not key:
        return default_model()
    if key in MODELS:
        return MODELS[key]
    if key == default_model().key:
        return default_model()
    raise UnknownModelError(f"unknown model {key!r}; choose one of: {', '.join(MODELS)}")


def api_key(model: Model) -> str | None:
    """The model's own key if set, else the shared NVIDIA_API_KEY."""
    return os.environ.get(model.api_key_env) or os.environ.get("NVIDIA_API_KEY") or None


def missing_key_message(model: Model) -> str:
    names = " or ".join(dict.fromkeys([model.api_key_env, "NVIDIA_API_KEY"]))
    return f"{model.label} has no API key: set {names} in .env (see .env.example)"


def _status_in_words(status: int) -> str:
    if status in (401, 403):
        return "the API key was rejected"
    if status == 404:
        return "the AI model wasn't found"
    if status == 429:
        return "too many requests; the AI provider asked us to slow down"
    if status >= 500:
        return "the AI service had a problem on its side"
    return "the AI service refused the request"


_THINKING = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def chat(
    messages: list[dict],
    *,
    model: str | None = None,
    temperature: float = 0,
    max_tokens: int = 1024,
    timeout: float = 60,
    purpose: str = "unspecified",
) -> str:
    """Send a chat request and return the reply text (without any reasoning).
    `purpose` says why the call is made; it goes in the log lines for this call."""
    chosen = resolve_model(model)
    key = api_key(chosen)
    if not key:
        log.warning("  Couldn't ask the AI to %s: no API key is set for %s", purpose, chosen.label)
        raise LLMError(missing_key_message(chosen))
    base_url = os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    log.info("  Asking the AI (%s) to %s…", chosen.label, purpose)
    started = time.monotonic()
    try:
        response = httpx.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            json={
                "model": chosen.id,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
                **chosen.extra,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        body = response.json()
        content = body["choices"][0]["message"]["content"] or ""
    except httpx.HTTPStatusError as e:
        log.warning(
            "  The AI didn't answer after %.1f s: %s (HTTP %s)",
            time.monotonic() - started, _status_in_words(e.response.status_code), e.response.status_code,
        )
        raise LLMError(f"LLM request failed: HTTP {e.response.status_code}: {e.response.text[:300]}") from None
    except httpx.TimeoutException:
        log.warning("  The AI didn't answer within %d s, so we stopped waiting", timeout)
        raise LLMError(f"LLM request timed out after {timeout}s") from None
    except httpx.ConnectError as e:
        log.warning("  Couldn't connect to the AI service at %s (no internet, or the address is wrong)", base_url)
        raise LLMError(f"LLM request failed: {e!r}") from None
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        log.warning("  Couldn't get an answer from the AI after %.1f s: %r", time.monotonic() - started, e)
        raise LLMError(f"LLM request failed: {e!r}") from None
    usage = body.get("usage") or {}
    log.info(
        "  The AI answered in %.1f s (%s tokens used)",
        time.monotonic() - started, usage.get("total_tokens", "?"),
    )
    # Reasoning normally arrives in a separate field; strip it if a model inlines it.
    return _THINKING.sub("", content).strip()
