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
       metadata.csv       git commit, model, digest, Ollama version + host, prompt version, snapshot id,
                          LLM GPU share, keep-alive

Snapshot: each config's snapshot id comes from its own run_metadata (recorded by the process
that ran it), so `--from` reports have it too. A report refuses to build (MixedSnapshots) when
its configs ran on different snapshots, or when run_all's record disagrees with a run's own.

Metric sources (no metric is reimplemented here)
  * Request understanding (field F1, exact match, clarification F1, silent-assumption rate) is
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
    freshness_mislabel_rate) are read from the harness's metric blocks. Their CI is taken from
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
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from clickless.config import load_config, repo_path
from clickless.contracts import PlanResult

from .metrics.request_understanding import (
    asked,
    clarification_prf,
    extraction_prf,
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
NA = "n/a"
_ERROR_STATUSES = ("error", "not_implemented")
LLM_PARSED = ("llm", "rules_fallback")
ERROR_STAGES = ("parse", "planning")
_REPORT_FILES = {INPUTS_FILE}

# (column, label, where the value comes from). "understanding" metrics are recomputed from rows;
# ("error_rate", stage) from the rows' error stages; ("block", path, key) metrics are read from
# the result JSON's metric blocks.
HEADLINE: tuple[tuple[str, str, tuple], ...] = (
    ("field_f1", "Field F1 (micro)", ("understanding", "micro_f1")),
    ("field_f1_strict", "Field F1 (strict)", ("understanding", "micro_f1_strict")),
    ("exact_match", "Exact match", ("understanding", "exact_match")),
    ("exact_match_strict", "Exact match (strict)", ("understanding", "exact_match_strict")),
    ("clarification_f1", "Clarification F1", ("understanding", "clarification_f1")),
    ("silent_assumption_rate", "Silent-assumption rate", ("understanding", "silent_assumption_rate")),
    ("parse_error_rate", "Parse error rate", ("error_rate", "parse")),
    ("planning_error_rate", "Planning error rate", ("error_rate", "planning")),
    ("plan_feasibility_rate", "Plan feasibility", ("block", ("planning", "feasibility"), "plan_feasibility_rate")),
    ("hard_violation_rate", "Hard-violation rate", ("block", ("planning", "feasibility"), "hard_violation_rate")),
    ("claim_support_rate", "Grounding (claim support)", ("block", ("grounding",), "claim_support_rate")),
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
    return str(snapshot_info(load_config(config))["snapshot_id"])


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
    the pipeline). Failures are recorded per config instead of aborting the whole run.
    """
    if not items_paths:
        check_split(split, final)
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
    cis = understanding_cis(items, results, n=n_boot)
    return {
        "micro_f1": (_num(ext["micro_f1"]), _ci(cis.get("micro_f1"))),
        "micro_f1_strict": (_num(ext["micro_f1_strict"]), _ci(cis.get("micro_f1_strict"))),
        "exact_match": (_num(ext["exact_match"]), _ci(cis.get("exact_match"))),
        "exact_match_strict": (_num(ext["exact_match_strict"]), _ci(cis.get("exact_match_strict"))),
        "clarification_f1": (_num(clar["f1"]), _ci(cis.get("clarification_f1"))),
        "silent_assumption_rate": (_num(silent["silent_assumption_rate"]), _ci(cis.get("silent_assumption_rate"))),
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
        else:
            block = _block(run.metrics, source[1])
            if block is None:
                out[column] = (None, None)
            else:
                ci = block.get("ci95", {})
                out[column] = (_num(block.get(source[2])), _ci(ci.get(source[2]) if isinstance(ci, Mapping) else None))
    return out


def per_item(run: ConfigRun, item_id: str) -> dict[str, Any]:
    """Per-item outcomes for paired tests: successes (bool) and scores (float or None)."""
    item, result = run.items.get(item_id), run.results.get(item_id)
    if item is None or result is None:
        return {
            "exact_match": False,
            "clarification_correct": False,
            "no_error": False,
            "field_f1": None,
            "latency_ms": run.latency.get(item_id),
        }
    counts = item_field_counts(item, result, level="city")  # primary, as in the headline
    f1 = prf(
        sum(c[0] for c in counts.values()), sum(c[1] for c in counts.values()), sum(c[2] for c in counts.values())
    )["f1"]
    return {
        "exact_match": item_exact_match(item, result),
        "clarification_correct": bool(item.clarification_required) == asked(result),
        "no_error": not run.errored(item_id),
        "field_f1": _num(f1),
        "latency_ms": run.latency.get(item_id),
    }


SUCCESS_OUTCOMES = ("exact_match", "clarification_correct", "no_error")
SCORE_OUTCOMES = ("field_f1", "latency_ms")


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
    """McNemar on success outcomes and Wilcoxon on per-item scores, over items both configs ran."""
    common = sorted(set(a.items) & set(b.items))
    pa = {i: per_item(a, i) for i in common}
    pb = {i: per_item(b, i) for i in common}
    rows: list[dict[str, Any]] = []
    for outcome in SUCCESS_OUTCOMES:
        xa = [bool(pa[i][outcome]) for i in common]
        xb = [bool(pb[i][outcome]) for i in common]
        test = mcnemar(xa, xb)
        rows.append(
            {
                "pair": f"{a.config} vs {b.config}",
                "outcome": outcome,
                "test": "McNemar",
                "n": len(common),
                "rate_a": sum(xa) / len(xa) if xa else None,
                "rate_b": sum(xb) / len(xb) if xb else None,
                "b": test["b"],
                "c": test["c"],
                "statistic": test["statistic"],
                "p_value": test["p_value"],
                "note": "exact binomial" if test["exact"] else "chi-square, continuity-corrected",
            }
        )
    for outcome in SCORE_OUTCOMES:
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
    ]
    _write_csv(run_dir / "breakdown.csv", br_rows, br_fields)

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
    lines += [
        "## Headline",
        "",
        "Value [95 % CI]. n/a = metric block missing or not implemented; [CI n/a] = the metric block reports no CI.",
        "Field F1 and exact match compare places at city level; (strict) also requires the gold's state, "
        "which annotators add for geocoding. Parse / planning error rates are over all items.",
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
             "EM (strict)", "Clarification F1", "Silent-assumption"],
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
                ]
                for r in rows
            ],
        )
        lines.append("")
    lines += ["## Findings (planner/oracle disagreements)", ""]
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
        ["Config", "Git commit", "Model", "Digest", "Ollama", "Prompt", "Snapshot"],
        [
            [
                r["config"],
                _fmt(r["git_commit"]) + (" (dirty)" if r["git_dirty"] else ""),
                _fmt(r["model"]),
                _fmt(r["model_digest"]),
                _fmt(r["ollama_version"]),
                _fmt(r["prompt_version"]),
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
    except (SplitRefused, MixedSnapshots, FixtureItemsRefused) as e:
        raise SystemExit(f"refused: {e}") from e


if __name__ == "__main__":
    main()
