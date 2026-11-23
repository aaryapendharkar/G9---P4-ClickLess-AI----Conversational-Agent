"""Keep the LLM loaded for a whole harness run and record how much of it sits on the GPU. Owner: Ian.

Latency comparisons between configs (A vs B, B vs C, ...) are only fair if each config's items run
with the model already loaded and with the same CPU/GPU split. Ollama fixes the split when it loads
a model (from the VRAM free at that moment) and unloads it after `keep_alive` idles (5 min by
default), so a run could start cold, or reload mid-run after a long oracle search, with a
different split. Measured on the deepti.jsonl shakedown: the same item, same tokens, took 1.4-2.0x
longer to parse in one run than in another.

  * warm_up(): before the first item, load the model and generate one token with
    keep_alive=KEEP_ALIVE (outside any timed region). Later /v1 requests renew that keep_alive
    instead of resetting it to the default (checked on Ollama 0.40.17).
  * before_item(): before each item, renew keep_alive (reloads the model if it was unloaded
    anyway, still outside the timed region) and read the model's GPU share,
    size_vram / size from /api/ps.
  * release(): after the run, set keep_alive back to RELEASE_KEEP_ALIVE so the model is not pinned
    in memory for hours.

Endpoint failures in before_item() never stop a run: the item records gpu_share None.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Mapping
from typing import Any

import requests

_LOG = logging.getLogger(__name__)

KEEP_ALIVE = "2h"  # longer than any single item, oracle included
RELEASE_KEEP_ALIVE = "5m"  # Ollama's default


class LLMResidency:
    def __init__(
        self,
        model: str,
        root: str,
        headers: Mapping[str, str] | None = None,
        http_post: Callable[..., Any] = requests.post,
        http_get: Callable[..., Any] = requests.get,
    ) -> None:
        self.model = model
        self.root = root.rstrip("/")
        self.headers = dict(headers or {})
        self.http_post = http_post
        self.http_get = http_get

    @classmethod
    def from_config(cls, cfg: Mapping[str, Any], env: Mapping[str, str] | None = None, **http) -> LLMResidency:
        from .harness import _ollama_root  # same root as run_metadata's /api/version and /api/tags

        env = os.environ if env is None else env
        llm = cfg.get("llm", {})
        base_url = env.get(llm.get("base_url_env", "VOYAGER_BASE_URL"), "")
        if not base_url:
            raise RuntimeError(f"config {cfg['name']} uses an LLM but {llm.get('base_url_env')} is not set")
        key = env.get(llm.get("api_key_env", "VOYAGER_API_KEY"))
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        return cls(llm.get("model"), _ollama_root(base_url), headers, **http)

    def _generate(self, body: Mapping[str, Any], timeout: float) -> None:
        resp = self.http_post(
            f"{self.root}/api/generate",
            json={"model": self.model, "stream": False, **body},
            headers=self.headers,
            timeout=timeout,
        )
        resp.raise_for_status()

    def gpu_share(self) -> float | None:
        """size_vram / size of the loaded model (1.0 = fully on the GPU); None if it isn't loaded."""
        resp = self.http_get(f"{self.root}/api/ps", headers=self.headers, timeout=10)
        resp.raise_for_status()
        names = {self.model, f"{self.model}:latest"}
        for m in resp.json().get("models", []):
            if (m.get("name") in names or m.get("model") in names) and m.get("size"):
                return round(float(m.get("size_vram", 0)) / float(m["size"]), 3)
        return None

    def warm_up(self) -> dict[str, Any]:
        """Load the model and generate one token; raises if the endpoint can't do it."""
        t0 = time.perf_counter()
        self._generate(
            {"prompt": "OK", "keep_alive": KEEP_ALIVE, "think": False, "options": {"num_predict": 1}},
            timeout=600,
        )
        return {
            "llm_warmup_s": round(time.perf_counter() - t0, 2),
            "llm_keep_alive": KEEP_ALIVE,
            "gpu_share_at_start": self.gpu_share(),
        }

    def before_item(self) -> dict[str, float | None]:
        try:
            self._generate({"keep_alive": KEEP_ALIVE}, timeout=600)  # no prompt: load/renew only
            return {"gpu_share": self.gpu_share()}
        except requests.RequestException as error:
            _LOG.warning("could not renew keep_alive / read /api/ps: %s", error)
            return {"gpu_share": None}

    def release(self) -> None:
        try:
            self._generate({"keep_alive": RELEASE_KEEP_ALIVE}, timeout=30)
        except requests.RequestException as error:
            _LOG.warning("could not reset keep_alive: %s", error)
