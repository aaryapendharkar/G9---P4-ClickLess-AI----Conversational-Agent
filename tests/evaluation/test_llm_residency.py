"""LLM warm-up, keep_alive and per-item GPU share (evaluation/llm_residency.py + harness wiring)."""

from __future__ import annotations

import json

import pytest
import requests

import evaluation.harness as harness
from clickless.config import load_config
from evaluation.llm_residency import KEEP_ALIVE, RELEASE_KEEP_ALIVE, LLMResidency

SMOKE = "benchmark/requests/dev/fixture_smoke.jsonl"


class _Resp:
    def __init__(self, payload=None):
        self.payload = payload or {}

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class FakeOllama:
    """Records /api/generate bodies; /api/ps reports `models` (or raises when `down`)."""

    def __init__(self, models=None, down=False):
        self.models = models if models is not None else [{"name": "qwen3:8b", "size": 6000, "size_vram": 4200}]
        self.down = down
        self.posts: list[tuple[str, dict]] = []

    def post(self, url, json=None, headers=None, timeout=None):
        if self.down:
            raise requests.ConnectionError("refused")
        self.posts.append((url, json))
        return _Resp({"done": True})

    def get(self, url, headers=None, timeout=None):
        if self.down:
            raise requests.ConnectionError("refused")
        assert url.endswith("/api/ps")
        return _Resp({"models": self.models})


def _residency(fake):
    return LLMResidency("qwen3:8b", "http://127.0.0.1:11434", http_post=fake.post, http_get=fake.get)


def test_warm_up_loads_the_model_with_a_long_keep_alive_and_records_the_split():
    fake = FakeOllama()
    meta = _residency(fake).warm_up()
    url, body = fake.posts[0]
    assert url == "http://127.0.0.1:11434/api/generate"
    assert body["model"] == "qwen3:8b" and body["keep_alive"] == KEEP_ALIVE and body["prompt"]
    assert body["options"] == {"num_predict": 1} and body["think"] is False
    assert meta["gpu_share_at_start"] == 0.7 and meta["llm_keep_alive"] == KEEP_ALIVE
    assert meta["llm_warmup_s"] >= 0


def test_warm_up_fails_loudly_when_the_endpoint_is_down():
    with pytest.raises(requests.ConnectionError):
        _residency(FakeOllama(down=True)).warm_up()


def test_before_item_renews_keep_alive_without_generating_and_reads_gpu_share():
    fake = FakeOllama(
        models=[{"name": "other:1b", "size": 10, "size_vram": 10}, {"model": "qwen3:8b", "size": 8, "size_vram": 8}]
    )
    assert _residency(fake).before_item() == {"gpu_share": 1.0}
    _, body = fake.posts[0]
    assert body == {"model": "qwen3:8b", "stream": False, "keep_alive": KEEP_ALIVE}  # no prompt


def test_before_item_records_none_instead_of_stopping_the_run():
    assert _residency(FakeOllama(models=[])).before_item() == {"gpu_share": None}  # model not loaded
    assert _residency(FakeOllama(down=True)).before_item() == {"gpu_share": None}  # endpoint down


def test_release_restores_the_default_keep_alive():
    fake = FakeOllama()
    _residency(fake).release()
    assert fake.posts[0][1]["keep_alive"] == RELEASE_KEEP_ALIVE
    _residency(FakeOllama(down=True)).release()  # never raises


def test_from_config_uses_the_native_root_without_credentials():
    cfg = load_config("B")
    env = {"VOYAGER_BASE_URL": "http://user:pw@127.0.0.1:11434/v1", "VOYAGER_API_KEY": "k"}
    r = LLMResidency.from_config(cfg, env)
    assert (r.model, r.root, r.headers) == ("qwen3:8b", "http://127.0.0.1:11434", {"Authorization": "Bearer k"})
    with pytest.raises(RuntimeError, match="VOYAGER_BASE_URL"):
        LLMResidency.from_config(cfg, {})


class _RecordingResidency:
    def __init__(self, shares):
        self.calls: list[str] = []
        self.shares = iter(shares)

    def warm_up(self):
        self.calls.append("warm_up")
        return {"llm_warmup_s": 1.5, "llm_keep_alive": KEEP_ALIVE, "gpu_share_at_start": 0.7}

    def before_item(self):
        self.calls.append("before_item")
        return {"gpu_share": next(self.shares)}

    def release(self):
        self.calls.append("release")


def _llm_run(monkeypatch, residency):
    """Run the harness on config A's pipeline, but treated as an LLM config (no endpoint needed)."""
    monkeypatch.setattr(harness, "_uses_llm", lambda cfg: True)
    monkeypatch.setattr(harness.LLMResidency, "from_config", classmethod(lambda cls, cfg: residency))
    monkeypatch.setattr(harness, "run_metadata", lambda cfg, text="": {"config": cfg["name"]})


def test_harness_warms_up_once_and_records_gpu_share_per_item(monkeypatch, tmp_path):
    residency = _RecordingResidency([0.7, 0.7, 0.5])
    _llm_run(monkeypatch, residency)
    payload = json.loads(harness.run("A", items_paths=[SMOKE], out_dir=str(tmp_path), n_boot=50).read_text())
    assert residency.calls == ["warm_up", "before_item", "before_item", "before_item", "release"]
    assert [r["gpu_share"] for r in payload["rows"]] == [0.7, 0.7, 0.5]
    assert payload["run_metadata"]["llm_keep_alive"] == KEEP_ALIVE
    assert payload["run_metadata"]["gpu_share_at_start"] == 0.7


def test_harness_releases_the_model_even_when_the_run_fails(monkeypatch, tmp_path):
    residency = _RecordingResidency([0.7])
    _llm_run(monkeypatch, residency)

    def boom(*a, **k):
        raise RuntimeError("pipeline crashed")

    monkeypatch.setattr(harness, "build_pipeline", lambda cfg: type("P", (), {"run": boom, "parser": object()})())
    with pytest.raises(RuntimeError, match="pipeline crashed"):
        harness.run("A", items_paths=[SMOKE], out_dir=str(tmp_path))
    assert residency.calls == ["warm_up", "before_item", "release"]


def test_configs_without_an_llm_record_no_gpu_share(tmp_path):
    payload = json.loads(harness.run("A", items_paths=[SMOKE], out_dir=str(tmp_path), n_boot=50).read_text())
    assert all("gpu_share" not in r for r in payload["rows"])
    assert "llm_keep_alive" not in payload["run_metadata"]
