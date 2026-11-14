"""Minimal client for ASU's OpenAI-compatible Voyager API. Owner: Deepti (also used by explain/).

Reads VOYAGER_BASE_URL and VOYAGER_API_KEY from the environment (.env, never committed).
Every call is logged with model + temperature so runs are reproducible.
"""

from __future__ import annotations

import os
from typing import Any

import requests


def chat(
    messages: list[dict[str, str]],
    model: str,
    temperature: float = 0.0,
    timeout_s: float = 60.0,
    **extra: Any,
) -> str:
    base = os.environ.get("VOYAGER_BASE_URL", "").rstrip("/")
    if not base:
        raise RuntimeError("VOYAGER_BASE_URL is not configured")
    key = os.environ.get("VOYAGER_API_KEY", "")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    resp = requests.post(
        f"{base}/chat/completions",
        headers=headers,
        json={"model": model, "messages": messages, "temperature": temperature, **extra},
        timeout=timeout_s,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"]
