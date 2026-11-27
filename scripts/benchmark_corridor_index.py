"""Benchmark the corridor grid against a BallTree prototype on the frozen snapshot (issue #190).

Measures, for every distinct route geometry in data/snapshot/routes/:
  * station index build time and memory (tracemalloc), once per method;
  * cold corridor-scan time per route (route memo cleared, median of --repeats runs);
  * how many station-segment distance evaluations each method performs;
  * peak memory of one scan, on the longest routes;
and checks that both methods return identical scans and identical candidates (max_candidates 60
and 10,000). Production is not changed: the pipeline keeps CorridorRetriever(method="indexed").

Run from the repository root with the frozen snapshot unzipped into data/snapshot/:

    python scripts/benchmark_corridor_index.py
    python scripts/benchmark_corridor_index.py --repeats 5 --out results/corridor-index/run.json

The script exits non-zero if any route differs between the two methods.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import sys
import time
import tracemalloc
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any

import sklearn

from clickless.config import load_config, repo_path
from clickless.contracts import Route
from clickless.data import DataStore
from clickless.data.snapshot import verify_manifest
from clickless.retrieval.corridor import CorridorRetriever
from clickless.retrieval.corridor_balltree import BallTreeCorridorRetriever
from evaluation.harness import git_commit
from evaluation.snapshot_id import snapshot_info

SNAPSHOT_DIR = "data/snapshot"
DEFAULT_OUT = "results/corridor-index/benchmark.json"
MEMORY_ROUTES = 5  # peak scan memory is measured on this many of the longest routes


def distinct_routes(routes_dir: Path) -> list[tuple[str, Route]]:
    """One (file name, Route) per distinct geometry, in file-name order."""
    seen: dict[tuple, tuple[str, Route]] = {}
    for path in sorted(routes_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.pop("_meta", None)
        route = Route.model_validate(payload)
        seen.setdefault(tuple((p.lat, p.lon) for p in route.geometry), (path.name, route))
    return list(seen.values())


def _poly(route: Route) -> list[tuple[float, float]]:
    return [(p.lat, p.lon) for p in route.geometry]


def grid_comparisons(retriever: CorridorRetriever, poly) -> int:
    stations = retriever._build_station_grid()
    return sum(len(segs) * len(stations.get(cell, ())) for cell, segs in retriever._segment_grid(poly).items())


def balltree_comparisons(retriever: BallTreeCorridorRetriever, poly) -> int:
    return sum(len(segs) for segs in retriever._segments_by_station(poly).values())


def _timed(fn) -> tuple[Any, float]:
    start = time.perf_counter()
    value = fn()
    return value, (time.perf_counter() - start) * 1000


def _traced(fn) -> tuple[Any, int, int]:
    """(value, retained bytes, peak bytes) allocated while fn runs."""
    tracemalloc.start()
    tracemalloc.reset_peak()
    before = tracemalloc.get_traced_memory()[0]
    value = fn()
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return value, current - before, peak - before


def _dump(cands) -> list[dict]:
    return [c.model_dump(mode="json") for c in cands]


def run(repeats: int, limit: int | None) -> dict[str, Any]:
    snapshot = repo_path(SNAPSHOT_DIR)
    problems = verify_manifest(snapshot)
    if problems:
        raise SystemExit(f"data/snapshot does not verify against its MANIFEST: {problems[:5]}")

    retrieval = load_config("A")["retrieval"]
    buffer_km, radius_m = retrieval["corridor_buffer_km"], retrieval["amenity_radius_m"]
    store = DataStore.from_dir(snapshot)
    routes = distinct_routes(snapshot / "routes")
    if limit is not None:
        routes = routes[:limit]

    def make(cls, max_candidates=60):
        return cls(store, buffer_km, radius_m, max_candidates)

    grid, tree = make(CorridorRetriever), make(BallTreeCorridorRetriever)
    for r in (grid, tree):
        r._check_store()

    _, grid_build_ms = _timed(grid._build_station_grid)
    grid._station_grid = None
    _, grid_bytes, grid_peak = _traced(grid._build_station_grid)
    _, tree_build_ms = _timed(tree._build_station_tree)
    tree._station_tree = None
    _, tree_bytes, tree_peak = _traced(tree._build_station_tree)

    # One retriever per (method, cap) for the candidate check; their indexes are built once.
    checkers = {mc: (make(CorridorRetriever, mc), make(BallTreeCorridorRetriever, mc)) for mc in (60, 10_000)}

    per_route: list[dict[str, Any]] = []
    mismatches: list[str] = []
    for name, route in routes:
        poly = _poly(route)
        g_runs, t_runs = [], []
        g_scan = t_scan = None
        for _ in range(repeats):  # alternate, so drift (thermal, caches) hits both methods alike
            g_scan, ms = _timed(partial(grid._scan, poly))
            g_runs.append(ms)
            t_scan, ms = _timed(partial(tree._scan, poly))
            t_runs.append(ms)
        identical_scan = g_scan == t_scan
        identical_cands = all(
            _dump(g.candidates(route, None)) == _dump(t.candidates(route, None)) for g, t in checkers.values()
        )
        if not (identical_scan and identical_cands):
            mismatches.append(name)
        per_route.append(
            {
                "route_file": name,
                "points": len(poly),
                "corridor_stations": len(g_scan or []),
                "grid_scan_ms": statistics.median(g_runs),
                "balltree_scan_ms": statistics.median(t_runs),
                "grid_comparisons": grid_comparisons(grid, poly),
                "balltree_comparisons": balltree_comparisons(tree, poly),
                "identical": identical_scan and identical_cands,
            }
        )

    longest = sorted(routes, key=lambda nr: -len(nr[1].geometry))[:MEMORY_ROUTES]
    scan_memory = []
    for name, route in longest:
        poly = _poly(route)
        _, _, g_peak = _traced(partial(grid._scan, poly))
        _, _, t_peak = _traced(partial(tree._scan, poly))
        scan_memory.append(
            {"route_file": name, "points": len(poly), "grid_peak_bytes": g_peak, "balltree_peak_bytes": t_peak}
        )

    def total(key: str) -> float:
        return sum(r[key] for r in per_route)

    speedups = [r["grid_scan_ms"] / r["balltree_scan_ms"] for r in per_route if r["balltree_scan_ms"] > 0]
    summary = {
        "routes": len(per_route),
        "identical_routes": sum(r["identical"] for r in per_route),
        "mismatched_routes": mismatches,
        "grid_scan_ms_total": total("grid_scan_ms"),
        "balltree_scan_ms_total": total("balltree_scan_ms"),
        "grid_scan_ms_median": statistics.median(r["grid_scan_ms"] for r in per_route),
        "balltree_scan_ms_median": statistics.median(r["balltree_scan_ms"] for r in per_route),
        "grid_scan_ms_max": max(r["grid_scan_ms"] for r in per_route),
        "balltree_scan_ms_max": max(r["balltree_scan_ms"] for r in per_route),
        "speedup_median": statistics.median(speedups) if speedups else None,
        "speedup_min": min(speedups) if speedups else None,
        "speedup_max": max(speedups) if speedups else None,
        "grid_comparisons_total": total("grid_comparisons"),
        "balltree_comparisons_total": total("balltree_comparisons"),
        "index_build_ms": {"grid": grid_build_ms, "balltree": tree_build_ms},
        "index_retained_bytes": {"grid": grid_bytes, "balltree": tree_bytes},
        "index_peak_bytes": {"grid": grid_peak, "balltree": tree_peak},
        "scan_peak_bytes_longest_routes": scan_memory,
    }
    return {
        "metadata": {
            "issue": 190,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "git_commit": git_commit(),
            **snapshot_info({"data": {"source": "snapshot", "snapshot_dir": SNAPSHOT_DIR}}, env={}),
            "stations": len(store.stations),
            "amenities": len(store.amenities),
            "corridor_buffer_km": buffer_km,
            "amenity_radius_m": radius_m,
            "repeats": repeats,
            "python": platform.python_version(),
            "platform": platform.platform(),
            "sklearn": sklearn.__version__,
        },
        "summary": summary,
        "routes": per_route,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repeats", type=int, default=3, help="timed runs per route and method (median)")
    parser.add_argument("--limit", type=int, default=None, help="only the first N distinct routes (smoke run)")
    parser.add_argument("--out", type=Path, default=repo_path(DEFAULT_OUT))
    args = parser.parse_args(argv)

    report = run(args.repeats, args.limit)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2, default=str))
    print(f"Report written to {args.out}")
    if report["summary"]["mismatched_routes"]:
        print(f"{len(report['summary']['mismatched_routes'])} route(s) differ between methods", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
