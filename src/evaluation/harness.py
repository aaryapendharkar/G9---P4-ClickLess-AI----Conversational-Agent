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

Errors: an exception from the pipeline no longer aborts the run. The row gets status "error"
(or "not_implemented"), the exception text, `error_type` (the exception class) and
`error_stage`: "parse" if it was raised inside the parser (no parsed request exists), else
"planning". For a planning exception the harness keeps the parsed request (it wraps the
pipeline's parser in a ParseRecorder) and records a PlanResult with status "error" and that
request, so the item counts as a planning error AND its parse is still scored for request
understanding. A PlanResult with status "error" from the pipeline itself (e.g. route not cached)
is a planning error too; the report derives its type from the result notes.

Fixture items (gold vehicle from the fixture world, e.g. benchmark/requests/dev/fixture_smoke.jsonl)
only make sense on fixture data. With CLICKLESS_DATA_SOURCE=snapshot (or a snapshot config),
explicit --items containing them are refused (FixtureItemsRefused); a --split run drops them and
lists them under "excluded_fixture_items" in the output, since dev/ always holds fixture_smoke.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import traceback
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

from clickless.config import load_config, repo_path
from clickless.contracts import PlanResult
from clickless.pipeline import build_pipeline

from .metrics.grounding import claim_support, freshness_mislabel_rate
from .metrics.request_understanding import (
    city_level_place_prf,
    clarification_prf,
    extraction_prf,
    missing_field_match,
    silent_assumption_rate,
    understanding_cis,
)
from .schema import BenchmarkItem, FixtureItemsRefused, load_items

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


# --------------------------------------------------------------------------- fixture guard


def data_source(cfg: Mapping[str, Any]) -> str:
    """'fixtures' or 'snapshot', with the same CLICKLESS_DATA_SOURCE override as DataStore."""
    return os.environ.get("CLICKLESS_DATA_SOURCE", "").strip().lower() or cfg["data"]["source"]


def fixture_vehicle_ids(cfg: Mapping[str, Any]) -> set[str]:
    path = repo_path(cfg["data"]["fixtures_dir"]) / "vehicles.json"
    if not path.is_file():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    records = data.get("records", []) if isinstance(data, dict) else data
    return {str(r["vehicle_id"]) for r in records if isinstance(r, dict) and r.get("vehicle_id")}


def is_fixture_item(item: BenchmarkItem, source_file: Path, fixture_vehicles: set[str]) -> bool:
    """A fixture_*.jsonl item, or one whose gold vehicle exists only in the fixture world."""
    return source_file.name.startswith("fixture_") or item.gold_request.get("vehicle_id") in fixture_vehicles


def _load_with_files(path: Path) -> list[tuple[BenchmarkItem, Path]]:
    files = sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
    return [(item, f) for f in files for item in load_items(f)]


def select_items(
    cfg: Mapping[str, Any], split: str, items_paths: list[str] | None
) -> tuple[list[BenchmarkItem], list[str]]:
    """(items to run, fixture item ids excluded). Raises FixtureItemsRefused for explicit
    fixture items on snapshot data."""
    paths = [repo_path(p) for p in items_paths] if items_paths else [repo_path(f"benchmark/requests/{split}")]
    loaded = [pair for p in paths for pair in _load_with_files(p)]
    if data_source(cfg) != "snapshot":
        return [i for i, _ in loaded], []
    vehicles = fixture_vehicle_ids(cfg)
    fixture = [(i, f) for i, f in loaded if is_fixture_item(i, f, vehicles)]
    if fixture and items_paths:
        ids = ", ".join(i.id for i, _ in fixture[:5]) + (" ..." if len(fixture) > 5 else "")
        files = ", ".join(sorted({f.name for _, f in fixture}))
        raise FixtureItemsRefused(
            f"refusing {len(fixture)} fixture-world item(s) ({ids}; from {files}) on snapshot data: their "
            "vehicles and places exist only in data/fixtures. Drop them from --items, or run them with "
            "CLICKLESS_DATA_SOURCE=fixtures."
        )
    excluded = [i.id for i, _ in fixture]
    if excluded:
        print(f"note: skipping {len(excluded)} fixture-world item(s) on snapshot data: {', '.join(excluded)}",
              file=sys.stderr)
    return [i for i, _ in loaded if i.id not in set(excluded)], excluded


# --------------------------------------------------------------------------- run


class ParseRecorder:
    """Wraps a parser and remembers its last result, so a request whose planning raises can still
    be scored on what was parsed. Everything else is delegated to the wrapped parser."""

    def __init__(self, parser: Any) -> None:
        self._parser = parser
        self.last: Any = None

    def parse(self, *args: Any, **kwargs: Any) -> Any:
        self.last = None
        self.last = self._parser.parse(*args, **kwargs)
        return self.last

    def __getattr__(self, name: str) -> Any:
        return getattr(self._parser, name)


def exception_stage(exc: BaseException, parser: Any) -> str:
    """'parse' if the exception was raised inside `parser` (a frame whose self is the parser), else 'planning'."""
    frames = traceback.walk_tb(exc.__traceback__)
    return "parse" if any(frame.f_locals.get("self") is parser for frame, _ in frames) else "planning"


def run(
    config: str = "A",
    split: str = "dev",
    limit: int | None = None,
    out_dir: str = "results",
    items_paths: list[str] | None = None,
    n_boot: int = 10_000,
) -> Path:
    cfg = load_config(config)
    items, excluded = select_items(cfg, split, items_paths)  # before any network call or pipeline build
    items = items[:limit]
    source = [str(p) for p in items_paths] if items_paths else [f"benchmark/requests/{split}"]
    cfg_path = _config_path(config)
    meta = run_metadata(cfg, cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else "")
    pipe = build_pipeline(cfg)
    recorder = pipe.parser = ParseRecorder(pipe.parser)  # keeps the parse if planning raises
    results, rows = [], []
    for item in items:
        t0 = time.perf_counter()
        recorder.last = None
        try:
            res = pipe.run(item.text, request_id=item.id)
        except Exception as e:  # noqa: BLE001 - one failing item must not abort the run; recorded per row
            stage = exception_stage(e, recorder)
            row = {"id": item.id, "status": "not_implemented" if isinstance(e, NotImplementedError) else "error",
                   "error": f"{type(e).__name__}: {e}", "error_type": type(e).__name__, "error_stage": stage}
            parsed = getattr(recorder.last, "request", None) if stage == "planning" else None
            if parsed is not None:  # planning failed after a successful parse: still score the parse
                res = PlanResult(request_id=item.id, config=cfg["name"], status="error", request=parsed,
                                 notes=[row["error"]])
                results.append(res)
                row.update(latency_ms=(time.perf_counter() - t0) * 1000, result=json.loads(res.model_dump_json()))
            rows.append(row)
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
        "grounding": _safe(claim_support, ordered),  # each result's own evidence only (ADR 0015)
        "freshness": _safe(freshness_mislabel_rate, ordered, pipe.store),
        # TODO(Ian): planning (oracle), tradeoffs, handoff, latency p50/p95
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label = "items" if items_paths else split
    out = repo_path(out_dir) / f"{cfg['name']}_{label}_{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"config": cfg["name"], "split": None if items_paths else split, "items": source,
                               "n": len(items), "excluded_fixture_items": excluded, "run_metadata": meta,
                               "metrics": metrics, "rows": rows},
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
    try:
        print(run(args.config, args.split, args.limit, out_dir=args.out, items_paths=args.items))
    except FixtureItemsRefused as e:
        raise SystemExit(f"refused: {e}") from e


if __name__ == "__main__":
    main()
