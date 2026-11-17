"""Harness run metadata: Config A is n/a, LLM configs record endpoint + digest, never the key."""

import json
from pathlib import Path

import pytest

from clickless.config import load_config
from clickless.nlu import llm_parser
from evaluation.harness import expected_digest, run_metadata

FULL = "500a1f067a9f" + "0" * 52
COMMIT = {"git_commit": "abc123", "git_dirty": False}
CONFIG_TEXT = "llm:\n  model: qwen3:8b        # pinned Config B/C model; Ollama digest 500a1f067a9f, Q4_K_M\n"


class _Resp:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def _fake_get(digest=FULL, calls=None):
    def get(url, headers=None, timeout=None):
        if calls is not None:
            calls.append((url, headers))
        if url.endswith("/api/version"):
            return _Resp({"version": "0.12.3"})
        if url.endswith("/api/tags"):
            return _Resp({"models": [{"name": "other:1b", "digest": "ffff"},
                                     {"name": "qwen3:8b", "model": "qwen3:8b", "digest": digest}]})
        raise AssertionError(url)
    return get


def _llm_cfg(**llm):
    return {"name": "B", "components": {"parser": "llm", "explainer": "template"},
            "llm": {"base_url_env": "BASE", "api_key_env": "KEY", "model": "qwen3:8b", **llm}}


ENV = {"BASE": "http://user:pw@localhost:11434/v1", "KEY": "sk-secret-key"}


def test_config_a_is_na_and_offline():
    def no_network(*a, **k):
        raise AssertionError("Config A must not call the network")

    meta = run_metadata(load_config("A"), env={}, http_get=no_network, commit=COMMIT)
    assert meta["config"] == "A" and meta["git_commit"] == "abc123"
    for k in ("prompt_version", "model", "ollama_version", "endpoint_host", "model_digest", "digest_check"):
        assert meta[k] == "n/a"


def test_llm_config_records_digest_from_config_comment():
    calls = []
    meta = run_metadata(_llm_cfg(), CONFIG_TEXT, env=ENV, http_get=_fake_get(calls=calls), commit=COMMIT)
    assert meta["model"] == "qwen3:8b" and meta["ollama_version"] == "0.12.3"
    assert meta["endpoint_host"] == "localhost:11434"
    assert meta["model_digest"] == FULL and meta["expected_digest"] == "500a1f067a9f"
    assert meta["digest_check"] == "match"
    assert meta["prompt_version"] == Path(llm_parser._PROMPT_PATH).stem  # the prompt the parser loads
    assert [u for u, _ in calls] == ["http://localhost:11434/api/version", "http://localhost:11434/api/tags"]
    dumped = json.dumps(meta)
    assert "sk-secret-key" not in dumped and "pw" not in dumped


def test_sha256_prefix_and_explicit_pin():
    meta = run_metadata(_llm_cfg(model_digest="500A1F067A9F"), env=ENV, http_get=_fake_get("sha256:" + FULL),
                        commit=COMMIT)
    assert meta["model_digest"] == FULL and meta["digest_check"] == "match"


def test_digest_mismatch_raises():
    with pytest.raises(RuntimeError, match="digest mismatch"):
        run_metadata(_llm_cfg(), CONFIG_TEXT, env=ENV, http_get=_fake_get("deadbeef" * 8), commit=COMMIT)


def test_unpinned_and_missing_model():
    meta = run_metadata(_llm_cfg(), "", env=ENV, http_get=_fake_get(), commit=COMMIT)
    assert meta["digest_check"] == "unpinned"
    with pytest.raises(RuntimeError, match="not listed"):
        run_metadata(_llm_cfg(model="llama3:8b"), CONFIG_TEXT, env=ENV, http_get=_fake_get(), commit=COMMIT)


def test_expected_digest_parsing():
    assert expected_digest({}, CONFIG_TEXT) == "500a1f067a9f"
    assert expected_digest({}, "model: CHANGE_ME") is None
