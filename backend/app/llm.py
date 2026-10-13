"""Minimal client for an OpenAI-compatible chat completions API.

Defaults to NVIDIA's hosted endpoint. Settings come from the environment, or
from the repo-root .env (see .env.example):

    NVIDIA_API_KEY   required
    LLM_BASE_URL     default https://integrate.api.nvidia.com/v1
    LLM_MODEL        default meta/llama-3.2-11b-vision-instruct
"""
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "meta/llama-3.2-11b-vision-instruct"


class LLMError(RuntimeError):
    pass


def chat(messages: list[dict], *, temperature: float = 0, max_tokens: int = 1024, timeout: float = 60) -> str:
    """Send a chat request and return the reply text."""
    key = os.environ.get("NVIDIA_API_KEY")
    if not key:
        raise LLMError("NVIDIA_API_KEY is not set; add it to .env (see .env.example)")
    base_url = os.environ.get("LLM_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    try:
        response = httpx.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            json={
                "model": os.environ.get("LLM_MODEL", DEFAULT_MODEL),
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": False,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"] or ""
    except httpx.HTTPStatusError as e:
        raise LLMError(f"LLM request failed: HTTP {e.response.status_code}: {e.response.text[:300]}") from None
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        raise LLMError(f"LLM request failed: {e!r}") from None
