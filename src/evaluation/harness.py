"""Evaluation harness: run one configuration over a benchmark split and save everything.

    python -m evaluation.harness --config A --split dev
    python -m evaluation.harness --config A --split raw        # before the split exists
    python -m evaluation.harness --config A --items a.jsonl b.jsonl --out results/prelim

Owner: Ian. Metrics that are not implemented yet are recorded as "not_implemented" so the harness
is usable from day one.

Saved per row, for metrics computed later from the saved outputs without a rerun (#184, #185):
  * `latency_ms` (also on error rows) and `result.timings_ms` per stage, including an "explain" lap;
    an error row also has `timings_ms` with the stages recorded before the exception.
  * `handoff_urls` and `expected_stops` ([{station_id, lat, lon}]) for each ok result. The harness
    approves a COPY of the result for the recommended option, so the saved PlanResult keeps
    approved=False, and the approval is logged to `<out>/<config>_handoff_audit.jsonl`, never to
    results/handoff_audit.jsonl. The handoff is not part of `latency_ms`.
  * `ranked_top3`: the ranker's ordered top 3 ([{itinerary_id, station_ids, total_min,
    amenities: [{name, category, wheelchair}]}]), which the trade-off alternatives do not preserve.

Every output records run metadata (`run_metadata`): config name, git commit, prompt version,
model, Ollama version, endpoint host (never the API key) and the full model digest from the
endpoint's /api/tags, checked against the short digest pinned in the config. Configurations
without an LLM component (Config A) record "n/a" for the LLM fields and make no network calls.

Every row records `parser_used` ("llm", "rules_fallback", or the non-LLM parser's name) and
`fallback_reason`, so items an LLM config silently parsed with rules are visible (parser_trace).

The run metadata also records the data the run used (`snapshot_id`, `snapshot_manifest_sha256`), so a report
built later with `--from` knows which snapshot each config ran on.

Errors: an exception from the pipeline no longer aborts the run. The row gets status "error"
(or "not_implemented"), the exception text, `error_type` (the exception class) and
`error_stage`: "parse" if it was raised inside the parser (no parsed request exists), else
"planning". For a planning exception the harness keeps the parsed request (it wraps the
pipeline's parser in a ParseRecorder) and records a PlanResult with status "error" and that
request, so the item counts as a planning error AND its parse is still scored for request
understanding. A PlanResult with status "error" from the pipeline itself (e.g. route not cached)
is a planning error too; the report derives its type from the result notes.

Fixture items (gold vehicle from the fixture world, e.g. benchmark/requests/dev/fixture_smoke.jsonl)
only make sense on fixture data. Split directories never load fixture_*.jsonl files (schema.load_items).
With CLICKLESS_DATA_SOURCE=snapshot (or a snapshot config), explicit --items containing fixture items
are refused (FixtureItemsRefused); a --split run drops any item whose gold vehicle exists only in the
fixture world and lists it under "excluded_fixture_items" in the output. The other way round, a --split
run on the fixture world is refused before any item runs (SnapshotRequired, #119): the splits' real
places and vehicles exist only in the snapshot. Fixture smoke runs pass --items instead.
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

from .config_names import ABLATIONS, SnapshotRequired, config_path, data_source, require_snapshot  # noqa: F401
from .llm_residency import LLMResidency
from .metrics.grounding import claim_support, freshness_mislabel_rate
from .metrics.ontology_adequacy import ontology_adequacy
from .metrics.planning import planning_metrics
from .metrics.request_understanding import (
    city_level_place_prf,
    clarification_prf,
    extraction_prf,
    invented_constraint_rate,
    missing_field_match,
    silent_assumption_rate,
    understanding_cis,
)
from .metrics.tradeoffs import tradeoff_metrics
from .oracle.label_setting import LabelSettingOracle
from .parser_trace import ParserTrace
from .schema import BenchmarkItem, FixtureItemsRefused, load_items
from .snapshot_id import snapshot_info

NA = "n/a"
_DIGEST_COMMENT = re.compile(r"digest\s+([0-9a-f]{12,64})\b", re.IGNORECASE)


def _safe(fn, *args) -> dict:
    """One metric block failing must not lose the others or the results JSON: the error is recorded
    in that block ({"error": "<Type>: <message>"}) and the run continues."""
    try:
        return fn(*args)
    except NotImplementedError as e:
        return {"not_implemented": str(e)}
    except Exception as e:  # noqa: BLE001 - isolated per metric block on purpose
        return {"error": f"{type(e).__name__}: {e}"}


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


# Config names and named ablations live in config_names (shared with the report); kept under these names.
_config_path = config_path


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
    meta.update(snapshot_info(cfg, env))
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
    # Same file selection as schema.load_items: a directory never contributes fixture_*.jsonl files.
    files = sorted(p for p in path.glob("*.jsonl") if not p.name.startswith("fixture_")) if path.is_dir() else [path]
    return [(item, f) for f in files for item in load_items(f)]


def select_items(
    cfg: Mapping[str, Any], split: str, items_paths: list[str] | None
) -> tuple[list[BenchmarkItem], list[str]]:
    """(items to run, fixture item ids excluded). Raises SnapshotRequired for a benchmark split on
    the fixture world (#119) and FixtureItemsRefused for explicit fixture items on snapshot data."""
    require_snapshot(cfg.get("name", "?"), cfg, split, items_paths)
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


class SelectRecorder:
    """Wraps the trade-off selector and remembers the ranker's ordered top 3 it was handed (#184).

    The selector receives the ranked (Itinerary, PlanCost) list; the saved alternatives are trade-off
    picks, not that order, so nDCG@3 needs this list. When planning falls back to a relaxation, the
    last call is the relaxation that was shown (MinCostRelaxer stops at the first feasible attempt).
    Each itinerary records its station ids and the amenities at its stops (name, category, wheelchair)
    from the run's own data store, so human raters can judge relevance without the snapshot."""

    def __init__(self, selector: Any, store: Any) -> None:
        self._selector = selector
        amenities = getattr(store, "amenities", None) or []
        self._amenities = {a.amenity_id: a for a in amenities}
        self.last: list[dict[str, Any]] = []

    def _amenity(self, amenity_id: str) -> dict[str, Any]:
        a = self._amenities.get(amenity_id)
        if a is None:
            return {"name": None, "category": None, "wheelchair": None}
        return {"name": a.name, "category": getattr(a.category, "value", a.category), "wheelchair": a.wheelchair}

    def summarize(self, ranked_plans: Any) -> list[dict[str, Any]]:
        top = []
        for itinerary, cost in list(ranked_plans)[:3]:
            amenity_ids = list(dict.fromkeys(a for stop in itinerary.stops for a in stop.amenity_ids))
            top.append({"itinerary_id": itinerary.itinerary_id,
                        "station_ids": [stop.station_id for stop in itinerary.stops],
                        "total_min": cost.total_min,
                        "amenities": [self._amenity(a) for a in amenity_ids]})
        return top

    def select(self, ranked_plans: Any, *args: Any, **kwargs: Any) -> Any:
        self.last = self.summarize(ranked_plans)
        return self._selector.select(ranked_plans, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._selector, name)


class TimedExplainer:
    """Wraps the explainer and adds an "explain" lap to the result's timings_ms (#185). The pipeline
    explains every result once, in _finish, after its other laps."""

    def __init__(self, explainer: Any) -> None:
        self._explainer = explainer

    def explain(self, result: Any, *args: Any, **kwargs: Any) -> Any:
        t0 = time.perf_counter()
        out = self._explainer.explain(result, *args, **kwargs)
        result.timings_ms["explain"] = round((time.perf_counter() - t0) * 1000, 2)
        return out

    def __getattr__(self, name: str) -> Any:
        return getattr(self._explainer, name)


class PlanRequestRecorder:
    """Remembers the PlanResult the pipeline is filling in, so a planning exception still saves the
    stage timings recorded so far (the pipeline writes each lap into result.timings_ms)."""

    def __init__(self, plan_request: Callable[..., Any]) -> None:
        self._plan_request = plan_request
        self.last: Any = None

    def __call__(self, req: Any, result: Any, *args: Any, **kwargs: Any) -> Any:
        self.last = result
        return self._plan_request(req, result, *args, **kwargs)


def handoff_audit_path(out_dir: str, config_name: str) -> Path:
    """Where the harness's offline approvals are logged: the run folder, never results/handoff_audit.jsonl."""
    return repo_path(out_dir) / f"{config_name}_handoff_audit.jsonl"


def instrument(pipe: Any, out_dir: str, config_name: str) -> dict[str, Any]:
    """Install the evaluation recorders on `pipe`. Fakes without a part simply skip that recorder."""
    hooks: dict[str, Any] = {"select": None, "plan": None}
    if hasattr(pipe, "selector"):
        hooks["select"] = pipe.selector = SelectRecorder(pipe.selector, getattr(pipe, "store", None))
    if hasattr(pipe, "explainer"):
        pipe.explainer = TimedExplainer(pipe.explainer)
    if hasattr(pipe, "plan_request"):
        hooks["plan"] = pipe.plan_request = PlanRequestRecorder(pipe.plan_request)
    if hasattr(pipe, "gate"):
        from clickless.handoff.approval import ApprovalGate

        pipe.gate = ApprovalGate(str(handoff_audit_path(out_dir, config_name)))
    return hooks


def offline_handoff(pipe: Any, result: PlanResult) -> dict[str, Any]:
    """Approve a COPY of an ok result for the recommended option and return the handoff fields (#185).

    pipe.approve() returns a copy, so the saved PlanResult keeps approved=False and no handoff_urls.
    The approval is logged to the run folder (instrument() replaces the gate). Expected stops are the
    recommended itinerary's stations with coordinates from the run's data store, for check_urls()."""
    try:
        approved = pipe.approve(result.model_copy(deep=True))
        stops = result.alternatives[0].itinerary.stops
        expected = []
        for stop in stops:
            station = pipe.store.station(stop.station_id)
            expected.append({"station_id": stop.station_id, "lat": station.lat, "lon": station.lon})
        return {"handoff_urls": list(approved.handoff_urls), "expected_stops": expected}
    except Exception as e:  # noqa: BLE001 - a handoff failure is recorded, never fatal to the run
        return {"handoff_urls": [], "expected_stops": [], "handoff_error": f"{type(e).__name__}: {e}"}


def exception_stage(exc: BaseException, parser: Any) -> str:
    """'parse' if the exception was raised inside `parser` (a frame whose self is the parser), else 'planning'."""
    frames = traceback.walk_tb(exc.__traceback__)
    return "parse" if any(frame.f_locals.get("self") is parser for frame, _ in frames) else "planning"


def kg_ontology_adequacy(cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Ontology adequacy (#158) for a config that builds the KG; {"not_applicable": ...} for the others.

    Runs once per harness run, not per item, on the fixture KG: the inventory's example bindings name
    fixture entities, so they are only valid there (metrics/ontology_adequacy.py). The KG code is
    imported here, not in the metric, which only counts."""
    if cfg.get("components", {}).get("retriever") != "corridor+kg":
        return {"not_applicable": f"config {cfg.get('name')} does not build the KG"}
    from clickless.data.loaders import DataStore
    from clickless.retrieval.kg.build import DEFAULT_ONTOLOGY, build_graph
    from clickless.retrieval.kg.competency import INVENTORY, load_inventory, run_inventory_example

    fixtures_dir = cfg["data"]["fixtures_dir"]
    graph = build_graph(DataStore.from_dir(fixtures_dir), cfg.get("kg", {}).get("ontology", DEFAULT_ONTOLOGY))
    version = json.loads(INVENTORY.read_text(encoding="utf-8")).get("version")
    block = ontology_adequacy(
        load_inventory(),
        lambda entry: run_inventory_example(graph, entry),
        inventory_version=version,
    )
    return {**block, "kg": f"fixtures ({fixtures_dir})"}


def build_oracle(cfg: Mapping[str, Any], pipe) -> LabelSettingOracle:
    """Oracle on the pipeline's frozen data. In OSRM mode the pipeline's router supplies ROUTES only
    (data access through the contracts Router protocol); detours always come from the oracle's own model."""
    osrm = cfg.get("routing", {}).get("mode") == "osrm"
    return LabelSettingOracle.from_config(cfg, store=pipe.store, route_source=pipe.router.route if osrm else None)


def run(
    config: str = "A",
    split: str = "dev",
    limit: int | None = None,
    out_dir: str = "results",
    items_paths: list[str] | None = None,
    n_boot: int = 10_000,
) -> Path:
    cfg_path = _config_path(config)
    cfg = load_config(str(cfg_path))
    items, excluded = select_items(cfg, split, items_paths)  # before any network call or pipeline build
    items = items[:limit]
    source = [str(p) for p in items_paths] if items_paths else [f"benchmark/requests/{split}"]
    meta = run_metadata(cfg, cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else "")
    pipe = build_pipeline(cfg)
    trace = ParserTrace(pipe.parser)
    # The recorder sits outside the trace: it keeps the parse if planning raises, while the trace
    # still sees the real parser (it patches the LLM parser's _fallback).
    recorder = pipe.parser = ParseRecorder(trace)
    hooks = instrument(pipe, out_dir, cfg["name"])
    # LLM configs: warm the model up and keep it loaded, and record its GPU share per item, so
    # latency compares like with like (evaluation/llm_residency.py).
    residency = LLMResidency.from_config(cfg) if _uses_llm(cfg) else None
    if residency is not None:
        meta.update(residency.warm_up())
    results, rows = [], []
    try:
        for item in items:
            gpu = residency.before_item() if residency is not None else {}
            t0 = time.perf_counter()
            trace.last = trace.empty()
            recorder.last = None
            for hook in hooks.values():
                if hook is not None:
                    hook.last = [] if isinstance(hook, SelectRecorder) else None
            try:
                res = pipe.run(item.text, request_id=item.id)
            except Exception as e:  # noqa: BLE001 - one failing item must not abort the run; recorded per row
                latency_ms = (time.perf_counter() - t0) * 1000
                stage = exception_stage(e, recorder)
                status = "not_implemented" if isinstance(e, NotImplementedError) else "error"
                # Failed items keep their latency and the stage timings recorded before the exception,
                # so they stay in the latency denominator (#185).
                partial = hooks["plan"].last if hooks["plan"] is not None else None
                timings = dict(getattr(partial, "timings_ms", None) or {})
                row = {"id": item.id, "status": status, "error": f"{type(e).__name__}: {e}",
                       "error_type": type(e).__name__, "error_stage": stage, "latency_ms": latency_ms,
                       "timings_ms": timings, **trace.last, **gpu}
                parsed = getattr(recorder.last, "request", None) if stage == "planning" else None
                if parsed is not None:  # planning failed after a successful parse: still score the parse
                    res = PlanResult(request_id=item.id, config=cfg["name"], status="error", request=parsed,
                                     notes=[row["error"]], timings_ms=timings)
                    results.append(res)
                    row.update(result=json.loads(res.model_dump_json()))
                rows.append(row)
                continue
            latency_ms = (time.perf_counter() - t0) * 1000  # before the offline handoff, which is not request latency
            results.append(res)
            row = {"id": item.id, "status": res.status, "latency_ms": latency_ms, **trace.last, **gpu,
                   "result": json.loads(res.model_dump_json()),
                   "ranked_top3": hooks["select"].last if hooks["select"] is not None else []}
            if res.status == "ok" and hasattr(pipe, "approve"):
                row.update(offline_handoff(pipe, res))
            rows.append(row)
    finally:
        if residency is not None:
            residency.release()
    by_id = {r.request_id: r for r in results}
    done = [i for i in items if i.id in by_id]
    ordered = [by_id[i.id] for i in done]
    oracle = build_oracle(cfg, pipe)
    metrics = {
        "extraction": _safe(extraction_prf, done, ordered),
        "extraction_city_level": _safe(city_level_place_prf, done, ordered),
        "clarification": _safe(clarification_prf, done, ordered),
        "missing_fields": _safe(missing_field_match, done, ordered),
        "silent_assumption": _safe(silent_assumption_rate, done, ordered),
        "invented_constraint": _safe(invented_constraint_rate, done, ordered),
        "bootstrap_ci95": _safe(understanding_cis, done, ordered, n_boot),
        "grounding": _safe(claim_support, ordered),  # each result's own evidence only (ADR 0015)
        "freshness": _safe(freshness_mislabel_rate, ordered, pipe.store),
        "ontology_adequacy": _safe(kg_ontology_adequacy, cfg),  # once per run, Config C only (#158)
        "planning": _safe(planning_metrics, done, ordered, oracle),
        "tradeoffs": _safe(tradeoff_metrics, done, ordered, oracle, cfg["tradeoffs"], cfg.get("evaluation")),
        # nDCG@3 (metrics.planning.ndcg_at_k) is wired once graded relevance labels exist.
        # TODO(Ian): handoff, latency p50/p95
    }
    # Planner/oracle disagreements are findings, reported on their own (docs/EVALUATION_ORACLE.md).
    findings = metrics["planning"].pop("findings", []) if isinstance(metrics["planning"], dict) else []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label = "items" if items_paths else split
    out = repo_path(out_dir) / f"{cfg['name']}_{label}_{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"config": cfg["name"], "split": None if items_paths else split, "items": source,
                               "n": len(items), "excluded_fixture_items": excluded, "run_metadata": meta,
                               "metrics": metrics, "findings": findings,
                               "handoff_audit": str(handoff_audit_path(out_dir, cfg["name"])), "rows": rows},
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
    except (FixtureItemsRefused, SnapshotRequired) as e:
        raise SystemExit(f"refused: {e}") from e


if __name__ == "__main__":
    main()
