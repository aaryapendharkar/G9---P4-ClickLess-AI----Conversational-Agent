"""Minimal client for ASU's OpenAI-compatible Voyager API. Owner: Deepti (also used by explain/).

Reads VOYAGER_BASE_URL and VOYAGER_API_KEY from the environment (.env, never committed).
Every call is logged with model + temperature so runs are reproducible.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

import requests


def chat(
    messages: list[dict[str, str]],
    model: str,
    temperature: float = 0.0,
    timeout_s: float = 60.0,
    base_url_env: str = "VOYAGER_BASE_URL",
    api_key_env: str = "VOYAGER_API_KEY",
    **extra: Any,
) -> str:
    base = os.environ.get(base_url_env, "").rstrip("/")
    if not base:
        raise RuntimeError(f"{base_url_env} is not configured")
    key = os.environ.get(api_key_env, "")
    host = urlparse(base).hostname
    is_local_endpoint = host in {"localhost", "127.0.0.1", "::1"}
    if not key and not is_local_endpoint:
        raise RuntimeError(f"{api_key_env} is required for non-local Voyager endpoints")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature, **extra}
    # Ollama's OpenAI-compatible endpoint ignores `think`, so think=False alone still lets qwen3
    # reason (~40 s per extraction); `reasoning_effort: "none"` is what turns it off there (#114).
    if extra.get("think") is False:
        payload.setdefault("reasoning_effort", "none")
    resp = requests.post(f"{base}/chat/completions", headers=headers, json=payload, timeout=timeout_s)
    resp.raise_for_status()
    try:
        return resp.json()["choices"][0]["message"]["content"]
    except (IndexError, KeyError, TypeError) as error:
        raise RuntimeError("Voyager response has an invalid chat completion shape") from error
