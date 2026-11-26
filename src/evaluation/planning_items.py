"""Backfill per-item planning outcomes for a run saved before the harness wrote them. Owner: Ian.

    python -m evaluation.planning_items results/<timestamp>

Harness runs now save `metrics.planning.feasibility.items` (metrics.planning.per_item_outcomes),
which the report needs for item-level CIs of plan feasibility and the hard-violation rate and for
the paired "got a valid plan" test. For an older run this recomputes them from the SAVED
PlanResults with the oracle only: the system under test is not run again (no parser, no planner,
no LLM). The outcomes go to results/<timestamp>/planning_items.json; the result JSONs are not
changed.

It refuses to write anything when
  * a config's current snapshot id differs from the one its run recorded (other data, other answers);
  * the recomputed totals do not reproduce the run's saved feasibility block (evaluable and
    oracle-feasible items, itineraries, plan feasibility, hard-violation rate), e.g. because the
    oracle changed since the run.
Pareto searches are not needed, so this is quick.
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from clickless.config import repo_path
from clickless.contracts import PlanResult
from clickless.data.loaders import DataStore

from .config_names import load_named_config
from .metrics.planning import evaluate, per_item_outcomes
from .oracle.label_setting import LabelSettingOracle
from .report import PLANNING_ITEMS_FILE, _items_for
from .snapshot_id import snapshot_info

CHECKED = ("n_evaluable", "n_oracle_feasible", "n_itineraries", "plan_feasibility_rate", "hard_violation_rate")


class BackfillMismatch(RuntimeError):
    """The recomputed outcomes do not reproduce what the run saved."""


def totals(outcomes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The feasibility block's totals, recomputed from per-item outcomes."""
    feasible = [o for o in outcomes if o["oracle_feasible"]]
    n_itin = sum(o["n_itineraries"] for o in outcomes)
    return {
        "n_evaluable": len(outcomes),
        "n_oracle_feasible": len(feasible),
        "n_itineraries": n_itin,
        "plan_feasibility_rate": sum(o["got_valid_plan"] for o in feasible) / len(feasible) if feasible else None,
        "hard_violation_rate": sum(o["n_violating_itineraries"] for o in outcomes) / n_itin if n_itin else None,
    }


def _differs(a: Any, b: Any) -> bool:
    if isinstance(a, int | float) and isinstance(b, int | float):
        return not math.isclose(float(a), float(b), rel_tol=0, abs_tol=1e-9)
    return a != b


def mismatches(saved: Mapping[str, Any], recomputed: Mapping[str, Any]) -> list[str]:
    return [f"{k}: saved {saved.get(k)!r}, recomputed {recomputed[k]!r}" for k in CHECKED
            if _differs(saved.get(k), recomputed[k])]


def outcomes_for(payload: Mapping[str, Any], oracle: LabelSettingOracle) -> list[dict[str, Any]]:
    """Per-item outcomes for one saved config run, from its own PlanResults."""
    items_by_id = _items_for(payload)
    rows = [r for r in payload["rows"] if r["id"] in items_by_id]
    items = [items_by_id[r["id"]] for r in rows]
    results = [PlanResult.model_validate(r["result"]) for r in rows if r.get("result") is not None]
    evals, _findings = evaluate(items, results, oracle)
    return per_item_outcomes(evals)


def _oracle(cfg: Mapping[str, Any]) -> LabelSettingOracle:
    if cfg.get("routing", {}).get("mode") == "osrm":
        raise BackfillMismatch(f"config {cfg['name']} routes through OSRM; backfill supports routing mode 'cache' only")
    return LabelSettingOracle.from_config(cfg, store=DataStore.from_config(dict(cfg)))


def _git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_path("."), capture_output=True, text=True)
    except OSError:
        return None
    return out.stdout.strip() or None


def backfill(run_dir: str | Path) -> Path:
    run_dir = Path(run_dir)
    configs: dict[str, Any] = {}
    for path in sorted(run_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if "config" not in payload or "rows" not in payload:
            continue
        config = payload["config"]
        saved = (payload.get("metrics", {}).get("planning") or {}).get("feasibility")
        if not isinstance(saved, Mapping) or "n_evaluable" not in saved:
            raise BackfillMismatch(f"{config}: {path.name} has no planning feasibility block to reproduce")
        if isinstance(saved.get("items"), list):
            print(f"{config}: {path.name} already has per-item outcomes; skipped")
            continue
        cfg = load_named_config(config)
        recorded = (payload.get("run_metadata") or {}).get("snapshot_id")
        current = snapshot_info(cfg)["snapshot_id"]
        if recorded and recorded != current:
            raise BackfillMismatch(f"{config}: the run used {recorded}, but the current data is {current}")
        outcomes = outcomes_for(payload, _oracle(cfg))
        recomputed = totals(outcomes)
        if diffs := mismatches(saved, recomputed):
            raise BackfillMismatch(f"{config}: recomputed outcomes do not match the run: " + "; ".join(diffs))
        configs[config] = {"source_json": path.name, "snapshot_id": current, "totals": recomputed, "items": outcomes}
        print(f"{config}: {len(outcomes)} items, totals match the saved block")
    out = run_dir / PLANNING_ITEMS_FILE
    out.write_text(
        json.dumps(
            {
                "note": "per-item planning outcomes recomputed with the oracle from the saved PlanResults "
                "(evaluation.planning_items); the system was not re-run",
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "git_commit": _git_commit(),
                "configs": configs,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m evaluation.planning_items")
    ap.add_argument("run_dir", help="results/<timestamp> with the saved result JSONs")
    args = ap.parse_args(argv)
    try:
        print(backfill(args.run_dir))
    except BackfillMismatch as e:
        raise SystemExit(f"refused: {e}") from e


if __name__ == "__main__":
    main()
