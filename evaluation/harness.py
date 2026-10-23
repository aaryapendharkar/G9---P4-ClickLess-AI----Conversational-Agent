"""Evaluation harness: run one configuration over a benchmark split and save everything.

    python -m evaluation.harness --config A --split dev
    python -m evaluation.harness --config A --split raw        # before the split exists

Owner: Ian. Never runs handoff (no approvals in offline experiments). Metrics that are not
implemented yet are recorded as "not_implemented" so the harness is usable from day one.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from clickless.config import load_config, repo_path
from clickless.pipeline import build_pipeline

from .metrics.request_understanding import extraction_prf, silent_assumption_rate
from .schema import load_items


def _safe(fn, *args) -> dict:
    try:
        return fn(*args)
    except NotImplementedError as e:
        return {"not_implemented": str(e)}


def run(config: str = "A", split: str = "dev", limit: int | None = None, out_dir: str = "results") -> Path:
    cfg = load_config(config)
    pipe = build_pipeline(cfg)
    items = load_items(repo_path(f"benchmark/requests/{split}"))[:limit]
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
    done = [i for i in items if any(r.request_id == i.id for r in results)]
    metrics = {
        "extraction": _safe(extraction_prf, done, results),
        "silent_assumption": _safe(silent_assumption_rate, done, results),
        # TODO(Ian): planning (oracle), grounding, tradeoffs, handoff, latency p50/p95
    }
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = repo_path(out_dir) / f"{cfg['name']}_{split}_{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"config": cfg["name"], "split": split, "n": len(items),
                               "metrics": metrics, "rows": rows}, indent=2, default=str))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="A")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()
    print(run(args.config, args.split, args.limit))


if __name__ == "__main__":
    main()
