"""Request latency p50 / p95 from a saved harness run (#185). Owner: Ian.

Timing boundaries (src/evaluation/harness.py, reviewed on #185):
  * `latency_ms` (per row) starts right before `pipeline.run()` and stops when it returns or raises:
    parse, route, retrieve, plan+verify, rank and explain. The LLM warm-up happens once before the
    first item and is NOT included. The offline handoff check runs after the clock stops and is
    NOT included either.
  * Per-stage laps come from `result.timings_ms` ({stage: ms}, written by the pipeline; the harness
    adds the "explain" lap). An error row keeps `latency_ms` and the stage laps recorded before the
    exception in its own `timings_ms`.

Every row counts, failures included: errored rows stay in the request-latency sample (their time
was spent), and their count is reported next to the percentiles. A row without `latency_ms` (a run
saved before #192) is counted as missing, never silently dropped. A stage's percentiles use the
rows that recorded that stage (a rejected request records parse and explain only). Request latency
is also given over the rows that reached routing, so early rejections do not hide planning time.

Percentiles: numpy's default linear interpolation (`numpy.percentile`, method "linear").

Latency is hardware-dependent: compare configs only within one run on one machine, and report the
machine alongside (the report's latency note; the LLM GPU share is in metadata.csv).
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np

# Pipeline order; any other recorded stage is listed after these, alphabetically.
STAGE_ORDER = ("parse", "route", "retrieve", "plan+verify", "rank", "explain")
ERROR_STATUSES = ("error", "not_implemented")


def _stats(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "p50": None, "p95": None, "mean": None, "max": None}
    arr = np.asarray(values, dtype=float)
    return {
        "n": int(arr.size),
        "p50": float(np.percentile(arr, 50)),
        "p95": float(np.percentile(arr, 95)),
        "mean": float(arr.mean()),
        "max": float(arr.max()),
    }


def _timings(row: Mapping[str, Any]) -> Mapping[str, Any]:
    """Stage laps of one row: the result's timings_ms, else the error row's own timings_ms."""
    result = row.get("result")
    if isinstance(result, Mapping) and result.get("timings_ms"):
        return result["timings_ms"]
    return row.get("timings_ms") or {}


def latency_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Request and per-stage latency (ms) over every row of one saved run."""
    timed = [row for row in rows if row.get("latency_ms") is not None]
    errors = [row for row in rows if row.get("status") in ERROR_STATUSES]
    stages: dict[str, list[float]] = {}
    for row in rows:
        for stage, ms in _timings(row).items():
            if ms is not None:
                stages.setdefault(str(stage), []).append(float(ms))
    order = [s for s in STAGE_ORDER if s in stages] + sorted(s for s in stages if s not in STAGE_ORDER)
    return {
        "n_rows": len(rows),
        "n_error_rows": len(errors),
        "n_error_rows_timed": sum(1 for row in errors if row.get("latency_ms") is not None),
        "missing_latency_ids": [row.get("id") for row in rows if row.get("latency_ms") is None],
        "request": _stats([float(row["latency_ms"]) for row in timed]),
        "request_ok_only": _stats([float(r["latency_ms"]) for r in timed if r.get("status") not in ERROR_STATUSES]),
        # Items that got past parsing to routing: without them, a config that rejects many requests
        # right after parsing has a request p50 of a few milliseconds.
        "request_routed": _stats([float(r["latency_ms"]) for r in timed if "route" in _timings(r)]),
        "stages": {stage: _stats(stages[stage]) for stage in order},
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Latency p50/p95 of saved harness runs.")
    parser.add_argument("runs", nargs="+", type=Path, help="harness output JSON files")
    for path in parser.parse_args(argv).runs:
        run = json.loads(path.read_text(encoding="utf-8"))
        print(json.dumps({"run": path.name, "config": run.get("config"), **latency_summary(run["rows"])}, indent=2))


if __name__ == "__main__":
    main()
