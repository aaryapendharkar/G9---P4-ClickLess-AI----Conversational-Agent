"""Evaluation report: run configs A/B/C on one split and write report.md + CSVs. Owner: Ian.

    python -m evaluation.report                         # A, B, C on dev -> results/<timestamp>/
    python -m evaluation.report --configs A --split dev --n-boot 2000
    python -m evaluation.report --split test --final    # only after the freeze (FROZEN.json)
    python -m evaluation.report --from results/<ts>     # rebuild the report from saved JSONs
    make eval-dev                                       # same as the first line

Steps
  1. run_all(): runs the harness once per config into results/<timestamp>/. A config that fails
     (e.g. B/C without an LLM endpoint) is recorded in report_inputs.json and shown as failed;
     the others still run. The test split is refused unless final=True AND benchmark/FROZEN.json
     exists, so nothing is ever tuned on held-out items by accident.
  2. build_report(): reads every result JSON in the directory and writes
       report.md          all sections below
       headline.csv       one row per config, value + 95 % CI per metric, LLM fallback counts
       paired.csv         A vs B, A vs C, B vs C, C vs C-noKG (when run): McNemar (success rates),
                          Wilcoxon (per-item scores)
       breakdown.csv      by author / category / route_type, with item counts
       errors.csv         errored items by stage (parse / planning) and error type
       findings.csv       planner/oracle disagreements (the result JSON's "findings")
       oracle_search.csv  per config: items whose oracle Pareto search stopped early (budget / max_labels)
       ontology_adequacy.csv  per config: competency questions answerable / 20, failing ids with
                          reasons, inventory version (Config C only; the others n/a)
       metadata.csv       git commit, model, digest, Ollama version + host, prompt version, snapshot id,
                          LLM GPU share, keep-alive

Snapshot: each config's snapshot id comes from its own run_metadata (recorded by the process
that ran it), so `--from` reports have it too. A report refuses to build (MixedSnapshots) when
its configs ran on different snapshots, or when run_all's record disagrees with a run's own.

Metric sources (no metric is reimplemented here)
  * Request understanding (field F1, exact match, clarification F1, silent-assumption rate,
    invented-constraint rate) is
    recomputed from the per-item rows with evaluation.metrics.request_understanding, and its CIs
    with understanding_cis (percentile bootstrap, seed 573). Field F1 and exact match use
    city-level place matching (primary); the "(strict)" columns also require the gold's state
    suffix, which exists for geocoding (see that module's docstring). Every item with a parsed
    request is scored, including items whose planning later failed.
  * Errors (status "error" or "not_implemented") are split by stage: parse_error_rate (no parsed
    request) and planning_error_rate (parsed, then failed, e.g. route not cached). The stage and
    type come from the harness row ("error_stage", "error_type"), else from the result: a
    request means planning, and a "no cached route" note means RouteNotCached. Rates are over
    all items, with CIs from evaluation.metrics.stats.bootstrap_ci.
  * Plan feasibility / hard-violation rate (metrics.planning.feasibility), grounding
    (metrics.grounding.claim_support_rate) and freshness (metrics.freshness.
    freshness_mislabel_rate) and ontology adequacy (metrics.ontology_adequacy, once per run, only
    for configs that build the KG) are read from the harness's metric blocks. Their CI is taken from
    the block's "ci95": {key: [lo, hi]} when the metric provides one, else shown as n/a. A block
    that is missing or {"not_implemented": ...} is shown as n/a.
  * Paired tests use evaluation.metrics.stats.mcnemar / wilcoxon on the items both configs ran.
  * LLM fallback rate: items whose row says parser_used == "rules_fallback" over the items an LLM
    parser handled ("llm" + "rules_fallback"). Any fallback puts a warning at the top of
    report.md, because those items measure the rules parser, not the configured LLM.

Independence: building a report imports no system code. Only run_all() imports the harness (which
runs the pipeline), lazily; tests/evaluation/test_report.py checks both.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from clickless.config import repo_path
from clickless.contracts import PlanResult

from .config_names import SnapshotRequired, load_named_config, require_snapshot
from .metrics.grounding import kg_claim_counts_item, kg_evidence_coverage_item
from .metrics.request_understanding import (
    asked,
    clarification_prf,
    extraction_prf,
    invented_constraint_rate,
    invented_constraints,
    item_exact_match,
    item_field_counts,
    prf,
    silent_assumption_rate,
    understanding_cis,
)
from .metrics.stats import bootstrap_ci, mcnemar, wilcoxon
from .schema import BenchmarkItem, FixtureItemsRefused, load_items
from .snapshot_id import snapshot_info

CONFIGS = ("A", "B", "C")
PAIRS = (
    ("A", "B"),
    ("A", "C"),  # the full system against the baseline
    ("B", "C"),
    ("C", "C-noKG"),  # the KG ablation (configs/config_c_nokg.yaml), when it was run
)
BREAKDOWNS = ("author", "category", "route_type")
INPUTS_FILE = "report_inputs.json"
# Per-item planning outcomes backfilled for a run saved before the harness wrote them
# (python -m evaluation.planning_items results/<ts>): {"configs": {config: {"items": [...]}}}.
PLANNING_ITEMS_FILE = "planning_items.json"
NA = "n/a"
_ERROR_STATUSES = ("error", "not_implemented")
LLM_PARSED = ("llm", "rules_fallback")
ERROR_STAGES = ("parse", "planning")
_REPORT_FILES = {INPUTS_FILE, PLANNING_ITEMS_FILE}
HARD_VIOLATION_NOTE = "measured against the gold request (includes parser errors)"

# (column, label, where the value comes from). "understanding" metrics are recomputed from rows;
# ("error_rate", stage) from the rows' error stages; ("block", path, key) metrics are read from
# the result JSON's metric blocks; ("planning", path, key) from the per-item planning outcomes
# (bootstrap CI over items), falling back to the block when a run has none; ("kg", key) from
# each result's own KG evidence and claims (metrics.grounding).
HEADLINE: tuple[tuple[str, str, tuple], ...] = (
    ("field_f1", "Field F1 (micro)", ("understanding", "micro_f1")),
    ("field_f1_strict", "Field F1 (strict)", ("understanding", "micro_f1_strict")),
    ("exact_match", "Exact match", ("understanding", "exact_match")),
    ("exact_match_strict", "Exact match (strict)", ("understanding", "exact_match_strict")),
    ("clarification_f1", "Clarification F1", ("understanding", "clarification_f1")),
    ("silent_assumption_rate", "Silent-assumption rate", ("understanding", "silent_assumption_rate")),
    ("invented_constraint_rate", "Invented-constraint rate", ("understanding", "invented_constraint_rate")),
    ("parse_error_rate", "Parse error rate", ("error_rate", "parse")),
    ("planning_error_rate", "Planning error rate", ("error_rate", "planning")),
    ("plan_feasibility_rate", "Plan feasibility", ("planning", ("planning", "feasibility"), "plan_feasibility_rate")),
    (
        "hard_violation_rate",
        "Hard-violation rate (vs gold)",
        ("planning", ("planning", "feasibility"), "hard_violation_rate"),
    ),
    ("claim_support_rate", "Grounding (claim support)", ("block", ("grounding",), "claim_support_rate")),
    ("kg_evidence_coverage", "KG evidence coverage", ("kg", "coverage")),
    ("kg_claim_share", "KG-cited claim share", ("kg", "claim_share")),
    ("freshness_mislabel_rate", "Freshness mislabel rate", ("block", ("freshness",), "freshness_mislabel_rate")),
)


class SplitRefused(ValueError):
    """Raised when the test split is requested without --final and a frozen benchmark."""


class MixedSnapshots(ValueError):
    """Raised when the configs in one report ran on different data (snapshots or fixtures)."""


# --------------------------------------------------------------------------- step 1: run


def check_split(split: str, final: bool, frozen_file: Path | None = None) -> None:
    """Refuse the held-out test split unless final=True and benchmark/FROZEN.json exists."""
    if split != "test":
        return
    frozen_file = frozen_file or repo_path("benchmark/FROZEN.json")
    if not final:
        raise SplitRefused("the test split is held out: pass --final (only for the final evaluation)")
    if not Path(frozen_file).is_file():
        raise SplitRefused(f"refusing the test split: {frozen_file} does not exist (freeze the benchmark first)")


def snapshot_id(config: str) -> str:
    """'fixtures', or 'snapshot:<first 12 hex of sha256(MANIFEST.json)> (<draft|frozen>, created <ts>)'."""
    return str(snapshot_info(load_named_config(config))["snapshot_id"])


def report_snapshots(
    configs: Sequence[str], runs: Mapping[str, ConfigRun], recorded: Mapping[str, str]
) -> dict[str, str | None]:
    """Snapshot id per config: the run's own run_metadata first, else run_all's record (results
    written before the harness recorded it). Raises MixedSnapshots if two configs differ, or if
    run_all's record disagrees with what the run itself recorded."""
    out: dict[str, str | None] = {}
    for config in configs:
        own = runs[config].meta.get("snapshot_id") if config in runs else None
        parent = recorded.get(config)
        if own and parent and own != parent:
            raise MixedSnapshots(f"config {config}: run_metadata says {own!r} but report_inputs.json says {parent!r}")
        out[config] = own or parent
    used = {c: s for c, s in out.items() if s and c in runs}
    if len(set(used.values())) > 1:
        detail = "; ".join(f"{c}: {s}" for c, s in sorted(used.items()))
        raise MixedSnapshots(f"configs in one report ran on different data ({detail})")
    return out


def run_all(
    configs: Sequence[str] = CONFIGS,
    split: str = "dev",
    final: bool = False,
    out_root: str = "results",
    items_paths: list[str] | None = None,
    n_boot: int = 10_000,
    runner: Callable[..., Path] | None = None,
    stamp: str | None = None,
) -> Path:
    """Run every config into <out_root>/<timestamp>/ and return that directory.

    `runner` defaults to evaluation.harness.run (imported here so building a report never loads
    the pipeline). Failures are recorded per config instead of aborting the whole run, except the
    refusals below, which apply to the whole run and are raised before anything runs or is written:
    the held-out test split without --final (check_split), and a split on the fixture world (#119).
    """
    if not items_paths:
        check_split(split, final)
        for config in configs:
            require_snapshot(config, load_named_config(config), split, items_paths)
    if runner is None:
        from .harness import run as runner  # runs the system under test; deliberately lazy
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = repo_path(out_root) / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    rel_dir = str(Path(out_root) / stamp)
    inputs: dict[str, Any] = {
        "split": None if items_paths else split,
        "items": items_paths,
        "final": final,
        "configs": list(configs),
        "snapshot_id": {},
        "failed": {},
        "outputs": {},
    }
    for config in configs:
        try:
            inputs["snapshot_id"][config] = snapshot_id(config)
            out = runner(config, split, None, out_dir=rel_dir, items_paths=items_paths, n_boot=n_boot)
            inputs["outputs"][config] = Path(out).name
        except FixtureItemsRefused:
            raise  # wrong items for this data source: applies to every config, so stop
        except Exception as e:  # noqa: BLE001 - one failing config must not hide the others
            inputs["failed"][config] = f"{type(e).__name__}: {e}"
    (run_dir / INPUTS_FILE).write_text(json.dumps(inputs, indent=2), encoding="utf-8")
    return run_dir


# --------------------------------------------------------------------------- step 2: load


class ConfigRun:
    """One config's saved result JSON, with its items and PlanResults paired by item id."""

    def __init__(self, payload: Mapping[str, Any], items_by_id: Mapping[str, BenchmarkItem]):
        self.payload = payload
        self.config: str = payload["config"]
        self.metrics: Mapping[str, Any] = payload.get("metrics", {}) or {}
        self.meta: Mapping[str, Any] = payload.get("run_metadata", {}) or {}
        self.findings: list[Mapping[str, Any]] = list(payload.get("findings", []) or [])
        self.rows: list[Mapping[str, Any]] = list(payload.get("rows", []))
        self.items: dict[str, BenchmarkItem] = {}
        self.results: dict[str, PlanResult] = {}
        self.status: dict[str, str] = {}
        self.latency: dict[str, float] = {}
        self.error: dict[str, tuple[str, str]] = {}  # item id -> (stage, type) for errored items
        # Per-item planning outcomes (metrics.planning.per_item_outcomes), keyed by item id: from
        # the feasibility block, or set by load_runs from planning_items.json for older runs.
        self.planning: dict[str, Mapping[str, Any]] = {}
        self.planning_source: str | None = None
        block = _block(self.metrics, ("planning", "feasibility"))
        if block is not None and isinstance(block.get("items"), list):
            self.set_planning(block["items"], "result JSON")
        for row in self.rows:
            item = items_by_id.get(row["id"])
            if item is None:
                continue
            self.items[item.id] = item
            self.status[item.id] = row.get("status", "")
            if row.get("latency_ms") is not None:
                self.latency[item.id] = float(row["latency_ms"])
            if row.get("result") is not None:
                self.results[item.id] = PlanResult.model_validate(row["result"])
            if self.errored(item.id):
                self.error[item.id] = error_stage_and_type(row, self.results.get(item.id))

    def set_planning(self, outcomes: Sequence[Mapping[str, Any]], source: str) -> None:
        self.planning = {str(o["item_id"]): o for o in outcomes}
        self.planning_source = source

    def verifier_disagreements(self) -> int:
        return sum(1 for f in self.findings if f.get("kind") == "verifier_disagreement")

    def pairs(self, ids: Sequence[str] | None = None) -> tuple[list[BenchmarkItem], list[PlanResult]]:
        """(items, results) for item ids that produced a PlanResult, in id order."""
        keep = sorted(i for i in (ids if ids is not None else self.results) if i in self.results)
        return [self.items[i] for i in keep], [self.results[i] for i in keep]

    def errored(self, item_id: str) -> bool:
        return self.status.get(item_id) in _ERROR_STATUSES or item_id not in self.results

    def errored_at(self, item_id: str, stage: str) -> bool:
        return self.error.get(item_id, ("", ""))[0] == stage


def error_stage_and_type(row: Mapping[str, Any], result: PlanResult | None) -> tuple[str, str]:
    """(stage, type) of one errored row: the harness's error_stage / error_type when recorded,
    else derived from the result (a parsed request means a planning error)."""
    stage = row.get("error_stage") or ("planning" if result is not None and result.request is not None else "parse")
    kind = row.get("error_type")
    if not kind:
        notes = " ".join(result.notes) if result is not None else ""
        if "no cached route" in notes:
            kind = "RouteNotCached"
        elif row.get("status") == "not_implemented":
            kind = "NotImplementedError"
        else:
            kind = "unknown"
    return str(stage), str(kind)


def _items_for(payload: Mapping[str, Any]) -> dict[str, BenchmarkItem]:
    sources = payload.get("items") or ([f"benchmark/requests/{payload['split']}"] if payload.get("split") else [])
    items: dict[str, BenchmarkItem] = {}
    for src in sources:
        for item in load_items(repo_path(src)):
            items[item.id] = item
    return items


def load_runs(run_dir: str | Path) -> tuple[dict[str, ConfigRun], dict[str, Any]]:
    """Every result JSON in run_dir (by config name) and the run inputs (if run_all wrote them)."""
    run_dir = Path(run_dir)
    inputs_path = run_dir / INPUTS_FILE
    inputs = json.loads(inputs_path.read_text(encoding="utf-8")) if inputs_path.is_file() else {}
    runs: dict[str, ConfigRun] = {}
    for path in sorted(run_dir.glob("*.json")):
        if path.name in _REPORT_FILES:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if "config" not in payload or "rows" not in payload:
            continue
        runs[payload["config"]] = ConfigRun(payload, _items_for(payload))
    backfill_path = run_dir / PLANNING_ITEMS_FILE
    if backfill_path.is_file():
        backfill = json.loads(backfill_path.read_text(encoding="utf-8"))
        for config, entry in (backfill.get("configs") or {}).items():
            run = runs.get(config)
            if run is not None and not run.planning:  # the result JSON's own outcomes win
                run.set_planning(entry.get("items") or [], f"{PLANNING_ITEMS_FILE} (backfilled)")
    return runs, inputs


# --------------------------------------------------------------------------- metrics


def _num(x: Any) -> float | None:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _ci(pair: Any) -> tuple[float, float] | None:
    if not isinstance(pair, (list, tuple)) or len(pair) != 2:
        return None
    lo, hi = _num(pair[0]), _num(pair[1])
    return None if lo is None or hi is None else (lo, hi)


def _block(metrics: Mapping[str, Any], path: Sequence[str]) -> Mapping[str, Any] | None:
    node: Any = metrics
    for key in path:
        if not isinstance(node, Mapping) or key not in node:
            return None
        node = node[key]
    if not isinstance(node, Mapping) or "not_implemented" in node:
        return None
    return node


def _understanding(items, results, n_boot: int) -> dict[str, tuple[float | None, tuple[float, float] | None]]:
    if not items:
        return {}
    ext = extraction_prf(items, results)
    clar = clarification_prf(items, results)
    silent = silent_assumption_rate(items, results)
    invented = invented_constraint_rate(items, results)
    cis = understanding_cis(items, results, n=n_boot)
    return {
        "micro_f1": (_num(ext["micro_f1"]), _ci(cis.get("micro_f1"))),
        "micro_f1_strict": (_num(ext["micro_f1_strict"]), _ci(cis.get("micro_f1_strict"))),
        "exact_match": (_num(ext["exact_match"]), _ci(cis.get("exact_match"))),
        "exact_match_strict": (_num(ext["exact_match_strict"]), _ci(cis.get("exact_match_strict"))),
        "clarification_f1": (_num(clar["f1"]), _ci(cis.get("clarification_f1"))),
        "silent_assumption_rate": (_num(silent["silent_assumption_rate"]), _ci(cis.get("silent_assumption_rate"))),
        "invented_constraint_rate": (
            _num(invented["invented_constraint_rate"]),
            _ci(cis.get("invented_constraint_rate")),
        ),
    }


def headline(run: ConfigRun, n_boot: int = 10_000) -> dict[str, tuple[float | None, tuple[float, float] | None]]:
    """column -> (value or None, CI or None) for every HEADLINE metric."""
    items, results = run.pairs()
    und = _understanding(items, results, n_boot)
    out: dict[str, tuple[float | None, tuple[float, float] | None]] = {}
    for column, _label, source in HEADLINE:
        if source[0] == "understanding":
            out[column] = und.get(source[1], (None, None))
        elif source[0] == "error_rate":
            errors = [float(run.errored_at(i, source[1])) for i in sorted(run.items)]
            out[column] = (sum(errors) / len(errors), _ci(bootstrap_ci(errors, n=n_boot))) if errors else (None, None)
        elif source[0] == "planning" and run.planning:
            out[column] = planning_metric(run, source[2], n_boot)
        elif source[0] == "kg":
            out[column] = kg_metric(run, source[1], n_boot)
        else:
            block = _block(run.metrics, source[1])
            if block is None:
                out[column] = (None, None)
            else:
                ci = block.get("ci95", {})
                out[column] = (_num(block.get(source[2])), _ci(ci.get(source[2]) if isinstance(ci, Mapping) else None))
    return out


def _ratio(records: Sequence[tuple[float, float]]) -> float:
    """sum(numerators) / sum(denominators) over (num, den) records; NaN when the denominator is 0."""
    den = sum(d for _n, d in records)
    return sum(n for n, _d in records) / den if den else float("nan")


def _mean_ci(values: list[float], n_boot: int) -> tuple[float | None, tuple[float, float] | None]:
    if not values:
        return None, None
    return sum(values) / len(values), _ci(bootstrap_ci(values, n=n_boot))


def _ratio_ci(records: list[tuple[float, float]], n_boot: int) -> tuple[float | None, tuple[float, float] | None]:
    """Pooled ratio with a CI that resamples ITEMS (an item's records stay together)."""
    if not records or not sum(d for _n, d in records):
        return None, None
    return _ratio(records), _ci(bootstrap_ci(records, n=n_boot, statistic=_ratio))


def planning_metric(run: ConfigRun, key: str, n_boot: int) -> tuple[float | None, tuple[float, float] | None]:
    """Plan feasibility (oracle-feasible items with a valid plan) or hard-violation rate (violating
    itineraries / itineraries, pooled) from the per-item outcomes, with a bootstrap CI over items."""
    outcomes = [run.planning[i] for i in sorted(run.planning)]
    if key == "plan_feasibility_rate":
        return _mean_ci([float(bool(o["got_valid_plan"])) for o in outcomes if o["oracle_feasible"]], n_boot)
    if key == "hard_violation_rate":
        records = [(float(o["n_violating_itineraries"]), float(o["n_itineraries"])) for o in outcomes]
        return _ratio_ci([r for r in records if r[1]], n_boot)
    raise ValueError(f"no per-item planning metric {key!r}")


def kg_metric(run: ConfigRun, key: str, n_boot: int) -> tuple[float | None, tuple[float, float] | None]:
    """KG evidence coverage (mean over items with a recommended charging plan) or the KG-cited
    claim share (pooled over claims), with a bootstrap CI over items."""
    results = [run.results[i] for i in sorted(run.results)]
    if key == "coverage":
        return _mean_ci([c for c in (kg_evidence_coverage_item(r) for r in results) if c is not None], n_boot)
    if key == "claim_share":
        records = [(float(k), float(n)) for n, k in (kg_claim_counts_item(r) for r in results) if n]
        return _ratio_ci(records, n_boot)
    raise ValueError(f"no KG metric {key!r}")


def per_item(run: ConfigRun, item_id: str) -> dict[str, Any]:
    """Per-item outcomes for paired tests: successes (bool) and scores (float or None)."""
    item, result = run.items.get(item_id), run.results.get(item_id)
    # Only oracle-feasible items can "get a valid plan"; None (left out) otherwise or when this
    # run has no per-item planning outcomes.
    outcome = run.planning.get(item_id)
    got_valid = bool(outcome["got_valid_plan"]) if outcome and outcome["oracle_feasible"] else None
    if item is None or result is None:
        return {
            "exact_match": False,
            "clarification_correct": False,
            "no_error": False,
            "no_invented_constraint": None,
            "got_valid_plan": got_valid,
            "field_f1": None,
            "latency_ms": run.latency.get(item_id),
            "kg_evidence_coverage": None,
            "kg_claim_share": None,
        }
    n_claims, n_kg = kg_claim_counts_item(result)
    counts = item_field_counts(item, result, level="city")  # primary, as in the headline
    f1 = prf(
        sum(c[0] for c in counts.values()), sum(c[1] for c in counts.values()), sum(c[2] for c in counts.values())
    )["f1"]
    return {
        "exact_match": item_exact_match(item, result),
        "clarification_correct": bool(item.clarification_required) == asked(result),
        "no_error": not run.errored(item_id),
        # None (left out of the paired test) when there is no parsed request to judge
        "no_invented_constraint": None if (inv := invented_constraints(item, result)) is None else not inv,
        "got_valid_plan": got_valid,
        "field_f1": _num(f1),
        "latency_ms": run.latency.get(item_id),
        "kg_evidence_coverage": kg_evidence_coverage_item(result),
        "kg_claim_share": n_kg / n_claims if n_claims else None,
    }


SUCCESS_OUTCOMES = ("exact_match", "clarification_correct", "no_error", "no_invented_constraint", "got_valid_plan")
SCORE_OUTCOMES = ("field_f1", "latency_ms")
# Score outcomes tested only for some pairs: the KG ablation is where KG evidence can differ.
PAIR_SCORE_OUTCOMES: dict[tuple[str, str], tuple[str, ...]] = {
    ("C", "C-noKG"): ("kg_evidence_coverage", "kg_claim_share"),
}


GPU_SHARE_TOLERANCE = 0.01  # shares further apart than this are a different CPU/GPU split


def gpu_share(run: ConfigRun) -> dict[str, Any]:
    """The LLM's GPU share (size_vram / size per item, from the harness) as n / min / median / max."""
    shares = sorted(float(r["gpu_share"]) for r in run.rows if r.get("gpu_share") is not None)
    if not shares:
        return {"n": 0, "min": None, "median": None, "max": None}
    return {"n": len(shares), "min": shares[0], "median": statistics.median(shares), "max": shares[-1]}


def gpu_share_warnings(shares: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """Latency is only comparable when every LLM config ran with the same CPU/GPU split."""
    recorded = {c: s for c, s in shares.items() if s["n"]}
    warnings = [
        f"{c}: the LLM's GPU share varied within the run ({s['min']:.2f}-{s['max']:.2f})"
        for c, s in recorded.items()
        if s["max"] - s["min"] > GPU_SHARE_TOLERANCE
    ]
    medians = [s["median"] for s in recorded.values()]
    if len(medians) > 1 and max(medians) - min(medians) > GPU_SHARE_TOLERANCE:
        warnings.insert(
            0,
            "the LLM's GPU share differs between configs ("
            + ", ".join(f"{c} {s['median']:.2f}" for c, s in recorded.items())
            + "), so latency comparisons between them are confounded",
        )
    return warnings


def paired(a: ConfigRun, b: ConfigRun) -> list[dict[str, Any]]:
    """McNemar on success outcomes and Wilcoxon on per-item scores, over items both configs ran.

    An outcome that is None for an item in either config (no_invented_constraint without a parsed
    request) leaves that item out of that outcome's test only.
    """
    common = sorted(set(a.items) & set(b.items))
    pa = {i: per_item(a, i) for i in common}
    pb = {i: per_item(b, i) for i in common}
    rows: list[dict[str, Any]] = []
    for outcome in SUCCESS_OUTCOMES:
        both = [i for i in common if pa[i][outcome] is not None and pb[i][outcome] is not None]
        xa = [bool(pa[i][outcome]) for i in both]
        xb = [bool(pb[i][outcome]) for i in both]
        if not both:  # mcnemar() would report p = 1 for no data, which reads as "no difference"
            rows.append(
                {"pair": f"{a.config} vs {b.config}", "outcome": outcome, "test": "McNemar", "n": 0,
                 "rate_a": None, "rate_b": None, "b": None, "c": None, "statistic": None, "p_value": None,
                 "note": "no paired values"}
            )
            continue
        test = mcnemar(xa, xb)
        rows.append(
            {
                "pair": f"{a.config} vs {b.config}",
                "outcome": outcome,
                "test": "McNemar",
                "n": len(both),
                "rate_a": sum(xa) / len(xa) if xa else None,
                "rate_b": sum(xb) / len(xb) if xb else None,
                "b": test["b"],
                "c": test["c"],
                "statistic": test["statistic"],
                "p_value": test["p_value"],
                "note": "exact binomial" if test["exact"] else "chi-square, continuity-corrected",
            }
        )
    for outcome in SCORE_OUTCOMES + PAIR_SCORE_OUTCOMES.get((a.config, b.config), ()):
        keep = [i for i in common if pa[i][outcome] is not None and pb[i][outcome] is not None]
        xa = [pa[i][outcome] for i in keep]
        xb = [pb[i][outcome] for i in keep]
        if keep:
            test = wilcoxon(xa, xb)
            stat, p, note = test["statistic"], test["p_value"], f"{test['n_nonzero']} non-zero differences"
        else:
            stat, p, note = None, None, "no paired values"
        rows.append(
            {
                "pair": f"{a.config} vs {b.config}",
                "outcome": outcome,
                "test": "Wilcoxon",
                "n": len(keep),
                "rate_a": sum(xa) / len(xa) if xa else None,
                "rate_b": sum(xb) / len(xb) if xb else None,
                "b": None,
                "c": None,
                "statistic": _num(stat),
                "p_value": _num(p),
                "note": note,
            }
        )
    return rows


def breakdown(run: ConfigRun) -> list[dict[str, Any]]:
    """Per author / category / route_type: item count, error rate and request-understanding metrics."""
    out: list[dict[str, Any]] = []
    for dim in BREAKDOWNS:
        groups: dict[str, list[str]] = {}
        for item_id, item in run.items.items():
            groups.setdefault(str(getattr(item, dim)), []).append(item_id)
        for group, ids in sorted(groups.items()):
            items, results = run.pairs(ids)
            ext = extraction_prf(items, results) if items else {}
            clar = clarification_prf(items, results) if items else {}
            silent = silent_assumption_rate(items, results) if items else {}
            invented = invented_constraint_rate(items, results) if items else {}
            out.append(
                {
                    "config": run.config,
                    "dimension": dim,
                    "group": group,
                    "n_items": len(ids),
                    "n_with_result": len(items),
                    **{f"{s}_error_rate": sum(run.errored_at(i, s) for i in ids) / len(ids) for s in ERROR_STAGES},
                    "field_f1": _num(ext.get("micro_f1")),
                    "field_f1_strict": _num(ext.get("micro_f1_strict")),
                    "exact_match": _num(ext.get("exact_match")),
                    "exact_match_strict": _num(ext.get("exact_match_strict")),
                    "clarification_f1": _num(clar.get("f1")),
                    "silent_assumption_rate": _num(silent.get("silent_assumption_rate")),
                    "invented_constraint_rate": _num(invented.get("invented_constraint_rate")),
                }
            )
    return out


def error_groups(run: ConfigRun) -> list[dict[str, Any]]:
    """Errored items grouped by (stage, type), with counts, shares of all items and example ids."""
    groups: dict[tuple[str, str], list[str]] = {}
    for item_id in sorted(run.error):
        groups.setdefault(run.error[item_id], []).append(item_id)
    n = len(run.items)
    return [
        {
            "config": run.config,
            "stage": stage,
            "error_type": kind,
            "n": len(ids),
            "share_of_items": len(ids) / n if n else None,
            "example_ids": " ".join(ids[:5]),
        }
        for (stage, kind), ids in sorted(groups.items())
    ]


# --------------------------------------------------------------------------- step 2: write


def parser_fallback(run: ConfigRun) -> dict[str, Any]:
    """LLM fallback counts from the rows' parser_used / fallback_reason (written by the harness).

    rate is None when no item was handled by an LLM parser (Config A, or results written before
    the harness recorded parser_used; "recorded" tells the two apart)."""
    used = [r.get("parser_used") for r in run.rows]
    llm_rows = [r for r in run.rows if r.get("parser_used") in LLM_PARSED]
    fell_back = [r for r in llm_rows if r.get("parser_used") == "rules_fallback"]
    reasons: dict[str, int] = {}
    for r in fell_back:
        reason = str(r.get("fallback_reason") or "no reason recorded")
        reasons[reason] = reasons.get(reason, 0) + 1
    parsers = sorted({u for u in used if u and u != "rules_fallback"} | ({"llm"} if fell_back else set()))
    return {
        "recorded": any(u is not None for u in used),
        "parsers": parsers,
        "n_llm_parsed": len(llm_rows),
        "n_fallback": len(fell_back),
        "rate": len(fell_back) / len(llm_rows) if llm_rows else None,
        "fallback_ids": sorted(r["id"] for r in fell_back),
        "reasons": sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0])),
    }


def _fallback_cell(fb: Mapping[str, Any]) -> list[str]:
    if not fb["recorded"]:
        return [NA, NA, NA, NA, "not recorded (results predate parser_used)"]
    if fb["rate"] is None:
        return [", ".join(fb["parsers"]) or NA, "0", "0", NA, "no LLM parser"]
    reasons = "; ".join(f"{n}x {_short(reason)}" for reason, n in fb["reasons"][:3]) or "-"
    return [", ".join(fb["parsers"]), str(fb["n_llm_parsed"]), str(fb["n_fallback"]), _fmt(fb["rate"]), reasons]


def _short(text: str, limit: int = 120) -> str:
    text = " ".join(text.split()).replace("|", "/")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _fmt(v: Any, digits: int = 3) -> str:
    v2 = _num(v) if not isinstance(v, str) else v
    if v2 is None:
        return NA
    return v2 if isinstance(v2, str) else f"{v2:.{digits}f}"


def _fmt_ci(value: float | None, ci: tuple[float, float] | None) -> str:
    if value is None:
        return NA
    return f"{value:.3f} [{ci[0]:.3f}, {ci[1]:.3f}]" if ci else f"{value:.3f} [CI n/a]"


def _md_table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return lines


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: Sequence[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(fields), lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: "" if r.get(k) is None else r.get(k) for k in fields})


def _error_summary(run: ConfigRun, ids: Sequence[str]) -> str:
    """'ValueError x2 (deepti-025: ValueError: ...)': error types by count, with the first example."""
    by_id = {r["id"]: r for r in run.rows}
    kinds = Counter(by_id.get(i, {}).get("error_type") or run.status.get(i) or "no result" for i in ids)
    first = next((i for i in ids if by_id.get(i, {}).get("error")), None)
    text = ", ".join(f"{k} x{n}" for k, n in kinds.most_common())
    return text + (f" (first: {first}: {by_id[first]['error']})" if first else "")


def run_problems(
    configs: Sequence[str], runs: Mapping[str, ConfigRun], failed: Mapping[str, str]
) -> tuple[list[str], list[str]]:
    """(why each config with nothing evaluated has nothing, item errors of configs that ran).

    A report whose headline is all n/a must say why at the top: the config failed before writing
    results, it ran no items, or every item errored. Configs where only some items errored are
    listed separately, so a crash is visible even when the rest of the run is fine."""
    nothing: list[str] = []
    item_errors: list[str] = []
    for config in configs:
        run = runs.get(config)
        if run is None:
            why = failed.get(config, "no result JSON in this directory")
            nothing.append(f"{config}: the config failed before writing results ({why})")
            continue
        if not run.items:
            nothing.append(f"{config}: no items ran (the item files or split matched no benchmark items)")
            continue
        errored = sorted(i for i in run.items if run.errored(i))
        if len(errored) == len(run.items):
            nothing.append(f"{config}: all {len(run.items)} items errored: {_error_summary(run, errored)}")
        elif errored:
            item_errors.append(f"{config}: {len(errored)} of {len(run.items)} items: {_error_summary(run, errored)}")
    return nothing, item_errors


def ontology_row(config: str, block: Mapping[str, Any] | None) -> dict[str, Any]:
    """One ontology_adequacy.csv row; "n/a: <why>" status for configs without a scored block."""
    row: dict[str, Any] = {"config": config, "answerable": None, "total": None, "ontology_adequacy": None,
                           "inventory_version": None, "kg": None, "failing_ids": "", "failing_reasons": ""}
    if not block:
        return {**row, "status": "n/a: no ontology_adequacy block"}
    if "not_applicable" in block:
        return {**row, "status": f"n/a: {block['not_applicable']}"}
    if "answerable" not in block:
        why = block.get("error") or block.get("not_implemented") or "no result"
        return {**row, "status": f"n/a: {why}"}
    failing = block.get("failing") or []
    return {
        **row,
        "status": "ok",
        "answerable": block["answerable"],
        "total": block.get("total"),
        "ontology_adequacy": block.get("ontology_adequacy"),
        "inventory_version": block.get("inventory_version"),
        "kg": block.get("kg"),
        "failing_ids": " ".join(str(f["id"]) for f in failing),
        "failing_reasons": "; ".join(f"{f['id']}: {f['reason']}" for f in failing),
    }


def build_report(run_dir: str | Path, n_boot: int = 10_000) -> Path:
    """Write report.md and the CSVs into run_dir; return the report.md path."""
    run_dir = Path(run_dir)
    runs, inputs = load_runs(run_dir)
    configs = list(inputs.get("configs") or sorted(runs))
    failed: dict[str, str] = dict(inputs.get("failed") or {})
    snapshot = report_snapshots(configs, runs, dict(inputs.get("snapshot_id") or {}))

    fallback = {c: parser_fallback(runs[c]) for c in configs if c in runs}

    # Headline
    head_rows: list[dict[str, Any]] = []
    md_head: list[list[str]] = []
    for config in configs:
        run = runs.get(config)
        values = headline(run, n_boot) if run else {}
        fb = fallback.get(config)
        row: dict[str, Any] = {
            "config": config,
            "n_items": len(run.items) if run else 0,
            "status": "ok" if run else ("failed: " + failed.get(config, "no result JSON")),
            "llm_parsed": fb["n_llm_parsed"] if fb and fb["recorded"] else None,
            "llm_fallbacks": fb["n_fallback"] if fb and fb["recorded"] else None,
            "llm_fallback_rate": fb["rate"] if fb else None,
        }
        md = [config, str(row["n_items"])]
        for column, _label, _src in HEADLINE:
            value, ci = values.get(column, (None, None))
            row[column] = value
            row[f"{column}_ci_lo"] = ci[0] if ci else None
            row[f"{column}_ci_hi"] = ci[1] if ci else None
            md.append(_fmt_ci(value, ci))
        head_rows.append(row)
        md_head.append(md)
    head_fields = ["config", "status", "n_items"] + [
        f for c, _l, _s in HEADLINE for f in (c, f"{c}_ci_lo", f"{c}_ci_hi")
    ] + ["llm_parsed", "llm_fallbacks", "llm_fallback_rate"]
    _write_csv(run_dir / "headline.csv", head_rows, head_fields)

    # Paired comparisons
    pair_rows: list[dict[str, Any]] = []
    skipped_pairs: list[str] = []
    for a, b in PAIRS:
        if a in runs and b in runs:
            pair_rows += paired(runs[a], runs[b])
        elif a in configs and b in configs:  # an ablation nobody asked for is not "missing"
            skipped_pairs.append(f"{a} vs {b}: n/a ({', '.join(c for c in (a, b) if c not in runs)} missing)")
    pair_fields = ["pair", "outcome", "test", "n", "rate_a", "rate_b", "b", "c", "statistic", "p_value", "note"]
    _write_csv(run_dir / "paired.csv", pair_rows, pair_fields)

    # Breakdowns
    br_rows = [r for c in configs if c in runs for r in breakdown(runs[c])]
    br_fields = [
        "config",
        "dimension",
        "group",
        "n_items",
        "n_with_result",
        "parse_error_rate",
        "planning_error_rate",
        "field_f1",
        "field_f1_strict",
        "exact_match",
        "exact_match_strict",
        "clarification_f1",
        "silent_assumption_rate",
        "invented_constraint_rate",
    ]
    _write_csv(run_dir / "breakdown.csv", br_rows, br_fields)

    # Invented hard constraints (#145), per item
    inv_rows = [
        {
            "config": c,
            "item_id": d["id"],
            "fields": " ".join(d["fields"]),
            "parsed_values": "; ".join(f"{k}={v}" for k, v in d["fields"].items()),
        }
        for c in configs
        if c in runs
        for d in invented_constraint_rate(*runs[c].pairs())["items"]
    ]
    _write_csv(run_dir / "invented_constraints.csv", inv_rows, ["config", "item_id", "fields", "parsed_values"])

    # Errors by stage and type
    err_rows = [r for c in configs if c in runs for r in error_groups(runs[c])]
    err_fields = ["config", "stage", "error_type", "n", "share_of_items", "example_ids"]
    _write_csv(run_dir / "errors.csv", err_rows, err_fields)

    # Findings
    find_rows = [
        {"config": c, "item_id": f.get("item_id"), "kind": f.get("kind"), "detail": f.get("detail")}
        for c in configs
        if c in runs
        for f in runs[c].findings
    ]
    _write_csv(run_dir / "findings.csv", find_rows, ["config", "item_id", "kind", "detail"])

    # Oracle search: items left out of the front-based metrics because the Pareto search stopped early
    search_fields = ["config", "n_items_searched", "n_incomplete", "incomplete_reasons", "pareto_time_budget_s",
                     "items_incomplete"]
    search_rows = []
    for c in configs:
        block = _block(runs[c].metrics, ("planning", "oracle_search")) if c in runs else None
        if block is not None:
            reasons = block.get("incomplete_reasons") or {}
            search_rows.append(
                {
                    "config": c,
                    "n_items_searched": block.get("n_items_searched"),
                    "n_incomplete": block.get("n_incomplete"),
                    "incomplete_reasons": ", ".join(f"{k}: {v}" for k, v in reasons.items()),
                    "pareto_time_budget_s": block.get("pareto_time_budget_s"),
                    "items_incomplete": " ".join(block.get("items_incomplete") or []),
                }
            )
    _write_csv(run_dir / "oracle_search.csv", search_rows, search_fields)

    # Ontology adequacy (#158): once per run, only for configs that build the KG
    onto_fields = ["config", "status", "answerable", "total", "ontology_adequacy", "inventory_version", "kg",
                   "snapshot_id", "failing_ids", "failing_reasons"]
    onto_rows = [
        {**ontology_row(c, runs[c].metrics.get("ontology_adequacy") if c in runs else None),
         "snapshot_id": snapshot.get(c)}
        for c in configs
    ]
    _write_csv(run_dir / "ontology_adequacy.csv", onto_rows, onto_fields)

    # Metadata
    shares = {c: gpu_share(runs[c]) for c in configs if c in runs}
    share_warnings = gpu_share_warnings(shares)
    meta_rows = []
    for config in configs:
        meta = runs[config].meta if config in runs else {}
        share = shares.get(config, {})
        meta_rows.append(
            {
                "config": config,
                "git_commit": meta.get("git_commit"),
                "git_dirty": meta.get("git_dirty"),
                "model": meta.get("model"),
                "model_digest": meta.get("model_digest"),
                "digest_check": meta.get("digest_check"),
                "ollama_version": meta.get("ollama_version"),
                "endpoint_host": meta.get("endpoint_host"),
                "prompt_version": meta.get("prompt_version"),
                "ranker": meta.get("ranker"),
                "ranker_model_path": meta.get("ranker_model_path"),
                "ranker_model_sha256": meta.get("ranker_model_sha256"),
                "snapshot_id": snapshot.get(config),
                "gpu_share_n": share.get("n", 0),
                "gpu_share_min": share.get("min"),
                "gpu_share_median": share.get("median"),
                "gpu_share_max": share.get("max"),
                "llm_keep_alive": meta.get("llm_keep_alive"),
                "llm_warmup_s": meta.get("llm_warmup_s"),
            }
        )
    meta_fields = [
        "config",
        "git_commit",
        "git_dirty",
        "model",
        "model_digest",
        "digest_check",
        "ollama_version",
        "endpoint_host",
        "prompt_version",
        "ranker",
        "ranker_model_path",
        "ranker_model_sha256",
        "snapshot_id",
        "gpu_share_n",
        "gpu_share_min",
        "gpu_share_median",
        "gpu_share_max",
        "llm_keep_alive",
        "llm_warmup_s",
    ]
    _write_csv(run_dir / "metadata.csv", meta_rows, meta_fields)

    # report.md
    split = inputs.get("split")
    lines = [
        f"# Evaluation report: {run_dir.name}",
        "",
        f"Split: {split or NA}"
        + (f" (items: {', '.join(inputs['items'])})" if inputs.get("items") else "")
        + (" -- FINAL (held-out test)" if inputs.get("final") else ""),
        f"Configs: {', '.join(configs) or NA}. Bootstrap: {n_boot} resamples, seed 573, percentile 95 % CI.",
        "",
    ]
    fell_back = [c for c in configs if c in fallback and fallback[c]["n_fallback"]]
    if fell_back:
        lines += ["> **Warning: LLM parser fallback.** These items were parsed by the rules fallback, "
                  "not the configured LLM, so their results measure rules parsing:", ">"]
        lines += [
            f"> - {c}: {fallback[c]['n_fallback']}/{fallback[c]['n_llm_parsed']} items "
            f"({fallback[c]['rate']:.1%}), e.g. {_short(fallback[c]['reasons'][0][0], 160)}"
            for c in fell_back
        ]
        lines += [">", "> See \"LLM parser fallback\" below.", ""]
    if share_warnings:
        lines += ["**Latency warning:**", ""] + [f"- {w}" for w in share_warnings] + [""]
    if failed:
        lines += ["**Failed configs:**", ""] + [f"- {c}: {msg}" for c, msg in sorted(failed.items())] + [""]
    excluded = {c: list(r.payload.get("excluded_fixture_items") or []) for c, r in runs.items()}
    if any(excluded.values()):
        lines += [
            "Fixture-world items skipped on snapshot data: "
            + "; ".join(f"{c}: {', '.join(ids)}" for c, ids in sorted(excluded.items()) if ids),
            "",
        ]
    nothing, item_errors = run_problems(configs, runs, failed)
    if nothing:
        if len(nothing) == len(configs):
            lines += ["**Nothing was evaluated in this run.** Every number below is n/a, for these reasons:", ""]
        else:
            lines += ["**Nothing evaluated for some configs:**", ""]
        lines += [f"- {msg}" for msg in nothing] + [""]
    if item_errors:
        lines += ["**Item errors** (counted in the error rate; the run continued):", ""]
        lines += [f"- {msg}" for msg in item_errors] + [""]
    lines += [
        "## Headline",
        "",
        "Value [95 % CI]. n/a = metric block missing or not implemented; [CI n/a] = the metric block reports no CI.",
        "Field F1 and exact match compare places at city level; (strict) also requires the gold's state, "
        "which annotators add for geocoding. Parse / planning error rates are over all items.",
        f"Hard-violation rate (vs gold): {HARD_VIOLATION_NOTE}; verifier disagreements: "
        + (", ".join(f"{c} {runs[c].verifier_disagreements()}" for c in configs if c in runs) or NA)
        + " (findings where the planner's verifier and the oracle, on the request the verifier saw, disagree).",
        "Plan feasibility and hard-violation CIs resample items: "
        + (
            "; ".join(f"{c} from {runs[c].planning_source}" for c in configs if c in runs and runs[c].planning)
            or "no per-item planning outcomes in this run, so no CI"
        )
        + ".",
        "KG evidence coverage: share of the recommended plan's displayed stop facts (station name, connector, "
        "power, amenities) supported by the result's own KG evidence, mean over items with a charging stop. "
        "KG-cited claim share: claims citing at least one KG fact, over all claims.",
        "",
    ]
    lines += _md_table(["Config", "n"] + [label for _c, label, _s in HEADLINE], md_head)
    lines += [
        "",
        "## LLM parser fallback",
        "",
        "Items an LLM parser handled, and how many it handed to the rules fallback (timeout, unreachable "
        "endpoint, or two invalid responses). Reasons are the parser's logged warnings.",
        "",
    ]
    lines += _md_table(
        ["Config", "Parser", "LLM-parsed", "Fallbacks", "Fallback rate", "Reasons"],
        [[c, *_fallback_cell(fallback[c])] if c in fallback else [c, NA, NA, NA, NA, "no result JSON"]
         for c in configs],
    )
    for c in fell_back:
        lines.append(f"- {c} fallback items: {', '.join(fallback[c]['fallback_ids'])}")
    lines += [
        "",
        "## Paired comparisons",
        "",
        "McNemar on per-item successes (b = first config only, c = second only); Wilcoxon "
        "signed-rank on per-item scores. Items both configs ran.",
        "",
    ]
    if pair_rows:
        lines += _md_table(
            ["Pair", "Outcome", "Test", "n", "First", "Second", "b", "c", "Statistic", "p"],
            [
                [
                    r["pair"],
                    r["outcome"],
                    r["test"],
                    str(r["n"]),
                    _fmt(r["rate_a"]),
                    _fmt(r["rate_b"]),
                    NA if r["b"] is None else str(r["b"]),
                    NA if r["c"] is None else str(r["c"]),
                    _fmt(r["statistic"]),
                    _fmt(r["p_value"], 4),
                ]
                for r in pair_rows
            ],
        )
    lines += [f"- {s}" for s in skipped_pairs]
    lines += ["", "## Errors by stage and type", ""]
    if err_rows:
        lines += _md_table(
            ["Config", "Stage", "Error type", "n", "Share of items", "Examples"],
            [
                [r["config"], r["stage"], r["error_type"], str(r["n"]), _fmt(r["share_of_items"]), r["example_ids"]]
                for r in err_rows
            ],
        )
    else:
        lines.append("No errors.")
    lines += ["", "## Breakdowns", ""]
    for dim in BREAKDOWNS:
        rows = [r for r in br_rows if r["dimension"] == dim]
        lines += [f"### By {dim}", ""]
        if not rows:
            lines += [NA, ""]
            continue
        lines += _md_table(
            ["Config", dim, "n", "Parse err", "Planning err", "Field F1", "F1 (strict)", "Exact match",
             "EM (strict)", "Clarification F1", "Silent-assumption", "Invented-constraint"],
            [
                [
                    r["config"],
                    r["group"],
                    str(r["n_items"]),
                    _fmt(r["parse_error_rate"]),
                    _fmt(r["planning_error_rate"]),
                    _fmt(r["field_f1"]),
                    _fmt(r["field_f1_strict"]),
                    _fmt(r["exact_match"]),
                    _fmt(r["exact_match_strict"]),
                    _fmt(r["clarification_f1"]),
                    _fmt(r["silent_assumption_rate"]),
                    _fmt(r["invented_constraint_rate"]),
                ]
                for r in rows
            ],
        )
        lines.append("")
    lines += [
        "## Invented constraints",
        "",
        "Items whose parsed request has a hard constraint (connector, minimum power, maximum detour, "
        "minimum arrival SoC) that the gold request does not have (#145). Separate from silent "
        "assumptions, which only cover incomplete requests.",
        "",
    ]
    if inv_rows:
        lines += _md_table(
            ["Config", "Item", "Invented", "Parsed values"],
            [[r["config"], r["item_id"], r["fields"], r["parsed_values"]] for r in inv_rows],
        )
    else:
        lines.append("None.")
    lines += [
        "",
        "## Oracle search",
        "",
        "Items whose oracle Pareto search stopped early (time budget or max_labels). They get an "
        "`oracle_search_incomplete` finding and are left out of the front-based metrics.",
        "",
    ]
    if search_rows:
        lines += _md_table(
            ["Config", "Items searched", "Incomplete", "Reasons", "Budget (s)", "Items"],
            [
                [
                    r["config"],
                    str(r["n_items_searched"]),
                    str(r["n_incomplete"]),
                    r["incomplete_reasons"] or "-",
                    "none" if r["pareto_time_budget_s"] is None else f"{r['pareto_time_budget_s']:g}",
                    r["items_incomplete"] or "-",
                ]
                for r in search_rows
            ],
        )
    else:
        lines.append("None recorded (no planning block with an oracle_search block in these results).")
    lines += [
        "",
        "## Ontology adequacy (competency questions on the fixture KG)",
        "",
        "Measures whether the ontology and queries can answer the CQs, not whether the snapshot data covers them.",
        "",
        "Competency questions (CQ1-CQ20, inventory.json) the KG answers: status \"tested\", the query "
        "runs, and it returns rows with the inventory's example bindings (or the inventory marks the "
        "question empty on fixtures). Run once per run on the fixture KG, where those bindings are "
        "valid; only configs that build the KG (C) are scored.",
        "",
    ]
    lines += _md_table(
        ["Config", "Answerable", "Share", "Inventory version", "Snapshot", "Failing"],
        [
            [
                r["config"],
                r["status"] if r["answerable"] is None else f"{r['answerable']}/{r['total']}",
                _fmt(r["ontology_adequacy"]),
                NA if r["inventory_version"] is None else str(r["inventory_version"]),
                r["snapshot_id"] or NA,
                r["failing_reasons"] or ("-" if r["answerable"] is not None else NA),
            ]
            for r in onto_rows
        ],
    )
    lines += ["", "## Findings (planner/oracle disagreements)", ""]
    if find_rows:
        lines += _md_table(
            ["Config", "Item", "Kind", "Detail"],
            [[r["config"], str(r["item_id"]), str(r["kind"]), str(r["detail"])] for r in find_rows],
        )
    else:
        lines.append("None recorded (no planning block with findings in these results).")
    lines += ["", "## LLM GPU residency", ""]
    lines += [
        "Share of the LLM held in GPU memory (size_vram / size from Ollama /api/ps), read before each "
        "item after the harness renewed keep_alive. Configs without an LLM record none.",
        "",
    ]
    lines += _md_table(
        ["Config", "Items recorded", "Min", "Median", "Max", "Keep-alive", "Warm-up (s)"],
        [
            [
                r["config"],
                str(r["gpu_share_n"]),
                _fmt(r["gpu_share_min"]),
                _fmt(r["gpu_share_median"]),
                _fmt(r["gpu_share_max"]),
                _fmt(r["llm_keep_alive"]),
                _fmt(r["llm_warmup_s"]),
            ]
            for r in meta_rows
        ],
    )
    lines += ["", "## Run metadata", ""]
    lines += _md_table(
        ["Config", "Git commit", "Model", "Digest", "Ollama", "Prompt", "Ranker", "Snapshot"],
        [
            [
                r["config"],
                _fmt(r["git_commit"]) + (" (dirty)" if r["git_dirty"] else ""),
                _fmt(r["model"]),
                _fmt(r["model_digest"]),
                _fmt(r["ollama_version"]),
                _fmt(r["prompt_version"]),
                _fmt(r["ranker"])
                + (f" ({_fmt(r['ranker_model_sha256'])})" if r["ranker"] == "logreg" else ""),
                _fmt(r["snapshot_id"]),
            ]
            for r in meta_rows
        ],
    )
    lines.append("")
    out = run_dir / "report.md"
    out.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return out


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m evaluation.report")
    ap.add_argument("--configs", nargs="+", default=list(CONFIGS))
    ap.add_argument("--split", default="dev")
    ap.add_argument("--final", action="store_true", help="allow the held-out test split (needs FROZEN.json)")
    ap.add_argument("--items", nargs="+", help="explicit .jsonl files/dirs instead of --split")
    ap.add_argument("--out", default="results")
    ap.add_argument("--n-boot", type=int, default=10_000)
    ap.add_argument("--from", dest="from_dir", help="rebuild the report from an existing results/<ts> directory")
    args = ap.parse_args(argv)
    try:
        run_dir = (
            Path(args.from_dir)
            if args.from_dir
            else run_all(args.configs, args.split, args.final, args.out, args.items, args.n_boot)
        )
        print(build_report(run_dir, n_boot=args.n_boot))
    except (SplitRefused, MixedSnapshots, FixtureItemsRefused, SnapshotRequired) as e:
        raise SystemExit(f"refused: {e}") from e


if __name__ == "__main__":
    main()
