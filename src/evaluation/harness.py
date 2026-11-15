"""Evaluation harness: run one configuration over a benchmark split and save everything.

    python -m evaluation.harness --config A --split dev
    python -m evaluation.harness --config A --split raw        # before the split exists
    python -m evaluation.harness --config A --items a.jsonl b.jsonl --out results/prelim

Owner: Ian. Never runs handoff (no approvals in offline experiments). Metrics that are not
implemented yet are recorded as "not_implemented" so the harness is usable from day one.

Every output records run metadata (`run_metadata`): config name, git commit, prompt version,
model, Ollama version, endpoint host (never the API key) and the full model digest from the
endpoint's /api/tags, checked against the short digest pinned in the config. Configurations
without an LLM component (Config A) record "n/a" for the LLM fields and make no network calls.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

from clickless.config import load_config, repo_path
from clickless.pipeline import build_pipeline

from .metrics.request_understanding import (
    city_level_place_prf,
    clarification_prf,
    extraction_prf,
    missing_field_match,
    silent_assumption_rate,
    understanding_cis,
)
from .schema import load_items

NA = "n/a"
_DIGEST_COMMENT = re.compile(r"digest\s+([0-9a-f]{12,64})\b", re.IGNORECASE)


def _safe(fn, *args) -> dict:
    try:
        return fn(*args)
    except NotImplementedError as e:
        return {"not_implemented": str(e)}


# --------------------------------------------------------------------------- run metadata


def git_commit() -> dict[str, Any]:
    def git(*args: str) -> str | None:
        try:
            out = subprocess.run(["git", *args], capture_output=True, text=True, check=True, cwd=repo_path("."))
            return out.stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            return None

    status = git("status", "--porcelain", "--untracked-files=no")
    return {"git_commit": git("rev-parse", "HEAD"), "git_dirty": None if status is None else bool(status)}


def _uses_llm(cfg: Mapping[str, Any]) -> bool:
    comp = cfg.get("components", {})
    return comp.get("parser") == "llm" or comp.get("explainer") == "grounded_llm"


def _config_path(config: str) -> Path:
    if config.upper() in {"A", "B", "C"}:
        return repo_path(f"configs/config_{config.lower()}.yaml")
    return repo_path(config)


def expected_digest(cfg: Mapping[str, Any], config_text: str = "") -> str | None:
    """Short model digest pinned for this config: llm.model_digest, else 'digest <hex>' in the YAML comments."""
    pinned = cfg.get("llm", {}).get("model_digest")
    if pinned:
        return str(pinned).lower()
    m = _DIGEST_COMMENT.search(config_text)
    return m.group(1).lower() if m else None


def _prompt_version(cfg: Mapping[str, Any]) -> str:
    if cfg.get("components", {}).get("parser") != "llm":
        return NA
    from clickless.nlu import llm_parser

    path = getattr(llm_parser, "_PROMPT_PATH", None)
    return Path(path).stem if path else "unknown"


def _ollama_root(base_url: str) -> str:
    """http://user:pw@host:11434/v1 -> http://host:11434 (credentials and the OpenAI /v1 suffix dropped)."""
    parts = urlsplit(base_url)
    netloc = parts.hostname or ""
    if parts.port:
        netloc += f":{parts.port}"
    path = re.sub(r"/v1/?$", "", parts.path.rstrip("/"))
    return f"{parts.scheme}://{netloc}{path}"


def endpoint_host(base_url: str) -> str:
    """host[:port] only -- never credentials, path or query."""
    parts = urlsplit(base_url)
    return (parts.hostname or "") + (f":{parts.port}" if parts.port else "")


def run_metadata(
    cfg: Mapping[str, Any],
    config_text: str = "",
    env: Mapping[str, str] | None = None,
    http_get: Callable[..., Any] = requests.get,
    commit: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Reproducibility record for one run. Raises RuntimeError if the endpoint's model digest
    does not start with the short digest pinned in the config."""
    env = os.environ if env is None else env
    meta: dict[str, Any] = {"config": cfg["name"], **(commit if commit is not None else git_commit())}
    meta["prompt_version"] = _prompt_version(cfg)
    if not _uses_llm(cfg):
        meta.update(model=NA, ollama_version=NA, endpoint_host=NA, model_digest=NA,
                    expected_digest=NA, digest_check=NA)
        return meta

    llm = cfg.get("llm", {})
    model = llm.get("model")
    base_url = env.get(llm.get("base_url_env", "VOYAGER_BASE_URL"), "")
    if not base_url:
        raise RuntimeError(f"config {cfg['name']} uses an LLM but {llm.get('base_url_env')} is not set")
    key = env.get(llm.get("api_key_env", "VOYAGER_API_KEY"))
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    root = _ollama_root(base_url)

    version = http_get(f"{root}/api/version", headers=headers, timeout=10)
    version.raise_for_status()
    tags = http_get(f"{root}/api/tags", headers=headers, timeout=10)
    tags.raise_for_status()
    names = {model, f"{model}:latest"}
    entry = next((m for m in tags.json().get("models", []) if m.get("name") in names or m.get("model") in names), None)
    if entry is None:
        raise RuntimeError(f"model {model!r} not listed by {endpoint_host(base_url)}/api/tags")
    digest = str(entry.get("digest", "")).lower().removeprefix("sha256:")
    expected = expected_digest(cfg, config_text)
    if expected is None:
        check = "unpinned"
    elif digest.startswith(expected):
        check = "match"
    else:
        raise RuntimeError(f"model digest mismatch for {model!r}: endpoint has {digest}, config pins {expected}")

    meta.update(model=model, ollama_version=version.json().get("version"), endpoint_host=endpoint_host(base_url),
                model_digest=digest, expected_digest=expected, digest_check=check)
    return meta


# --------------------------------------------------------------------------- run


def run(
    config: str = "A",
    split: str = "dev",
    limit: int | None = None,
    out_dir: str = "results",
    items_paths: list[str] | None = None,
    n_boot: int = 10_000,
) -> Path:
    cfg = load_config(config)
    cfg_path = _config_path(config)
    meta = run_metadata(cfg, cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else "")
    pipe = build_pipeline(cfg)
    if items_paths:
        items = [i for p in items_paths for i in load_items(repo_path(p))][:limit]
        source = [str(p) for p in items_paths]
    else:
        items = load_items(repo_path(f"benchmark/requests/{split}"))[:limit]
        source = [f"benchmark/requests/{split}"]
    results, rows = [], []
    for item in items:
        t0 = time.perf_counter()
        try:
            res = pipe.run(item.text, request_id=item.id)
        except NotImplementedError as e:
            rows.append({"id": item.id, "status": "not_implemented", "error": str(e)})
            continue
        results.append(res)
        rows.append({"id": item.id, "status": res.status, "latency_ms": (time.perf_counter() - t0) * 1000,
                     "result": json.loads(res.model_dump_json())})
    by_id = {r.request_id: r for r in results}
    done = [i for i in items if i.id in by_id]
    ordered = [by_id[i.id] for i in done]
    metrics = {
        "extraction": _safe(extraction_prf, done, ordered),
        "extraction_city_level": _safe(city_level_place_prf, done, ordered),
        "clarification": _safe(clarification_prf, done, ordered),
        "missing_fields": _safe(missing_field_match, done, ordered),
        "silent_assumption": _safe(silent_assumption_rate, done, ordered),
        "bootstrap_ci95": _safe(understanding_cis, done, ordered, n_boot),
        # TODO(Ian): planning (oracle), grounding, tradeoffs, handoff, latency p50/p95
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label = "items" if items_paths else split
    out = repo_path(out_dir) / f"{cfg['name']}_{label}_{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"config": cfg["name"], "split": None if items_paths else split, "items": source,
                               "n": len(items), "run_metadata": meta, "metrics": metrics, "rows": rows},
                              indent=2, default=str))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="A")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--items", nargs="+", help="explicit .jsonl files/dirs instead of --split")
    ap.add_argument("--out", default="results")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    print(run(args.config, args.split, args.limit, out_dir=args.out, items_paths=args.items))


if __name__ == "__main__":
    main()
