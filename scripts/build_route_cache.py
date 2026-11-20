"""Build the OSRM route cache for every benchmark origin -> destination pair (issue #36). Owner: Ian.

    python scripts/build_route_cache.py --dry-run                      # list pairs and slugs, no network
    python scripts/build_route_cache.py                                # build into data/snapshot/routes/
    python scripts/build_route_cache.py --extra-items path/to/manas.jsonl --dry-run

What it does
  1. Collects the gold origin/destination of every item in benchmark/requests/raw/*.jsonl (plus
     any --extra-items files, e.g. a teammate's file not merged yet). Items without both places
     (e.g. "incomplete" items) are listed as skipped.
  2. Resolves each place with DataStore.geocode (the snapshot's closed gazetteer, places.json),
     exactly as the parsers do. A pair with an unresolved place is skipped with its reason.
  3. Names files the way CachedRouter looks them up:
         routes/<slug(origin.text)>__<slug(destination.text)>.json
     with planning.routing._slug (lower-case; every run of other characters -> "-"), on the
     resolved text, e.g. "Flagstaff, Az" -> "flagstaff-az".
     Aliases: the gazetteer resolves both "Flagstaff" and "Flagstaff, AZ" to the same point,
     and a parser keeps whichever form the driver wrote ("Flagstaff" vs "Flagstaff, Az"). So by
     default every alias combination of a pair gets a copy of the same route (at most 2 x 2);
     --canonical-only writes just the gold form.
  4. Fetches each pair once from OSRM (default: the public demo server, router.project-osrm.org)
     with clickless.planning.routing.OSRMRouter, at most one request per --min-interval seconds,
     retrying temporary failures and HTTP 429 with backoff. Existing files are never re-fetched
     or overwritten; missing alias copies of an existing route are filled from it.
  5. Each file follows the Route contract. Provenance is the snapshot convention: source "OSRM",
     source_url (the exact request), retrieved_at, status "cached". `_meta` (ignored by
     CachedRouter) records the place texts, the benchmark item ids and the server.
  6. Writes a summary (pairs total / built / skipped / failed with reasons) to --summary and stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from clickless.config import repo_path
from clickless.contracts import DataStatus, Place, Route
from clickless.data import DataStore
from clickless.planning.routing import (
    OSRMNoRouteError,
    OSRMResponseError,
    OSRMRouteError,
    OSRMRouter,
    OSRMTemporaryError,
    _slug,
)

DEFAULT_OSRM = "https://router.project-osrm.org"


# --------------------------------------------------------------------------- collect


@dataclass
class Pair:
    origin: Place  # resolved (canonical gold form)
    destination: Place
    origin_aliases: list[str]  # every resolved text that names the same point
    destination_aliases: list[str]
    item_ids: list[str] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, str]:
        return self.origin.text, self.destination.text

    def filenames(self, canonical_only: bool) -> list[str]:
        if canonical_only:
            return [route_filename(self.origin.text, self.destination.text)]
        return [route_filename(o, d) for o in self.origin_aliases for d in self.destination_aliases]


def route_filename(origin_text: str, destination_text: str) -> str:
    """The exact name CachedRouter.route reads for these resolved place texts."""
    return f"{_slug(origin_text)}__{_slug(destination_text)}.json"


def read_items(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """JSON objects of one .jsonl file (// comment lines skipped) and per-line problems."""
    items, problems = [], []
    for n, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("//"):
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError as e:
            problems.append(f"{path.name}:{n}: not JSON ({e.msg})")
            continue
        if not isinstance(obj, dict) or "id" not in obj:
            problems.append(f"{path.name}:{n}: not a benchmark item")
            continue
        items.append(obj)
    return items, problems


def aliases(store: DataStore, place: Place) -> list[str]:
    """Resolved texts of every gazetteer key at this place's point, canonical form first.

    Canonical = the "City, St" form when there is one (what gold annotations use), so a gold
    "Flagstaff" and a gold "Flagstaff, AZ" name the same pair.
    """
    point = (place.lat, place.lon)
    texts = {store.geocode(k).text for k, v in store.places.items() if v == point} | {place.text}
    return sorted(texts, key=lambda t: ("," not in t, -len(t), t))


def _canonical(store: DataStore, place: Place) -> tuple[Place, list[str]]:
    names = aliases(store, place)
    return place.model_copy(update={"text": names[0]}), names


def collect_pairs(
    store: DataStore, files: Iterable[Path]
) -> tuple[list[Pair], list[dict[str, str]], list[str]]:
    """(unique resolved pairs, skipped items with reasons, file problems)."""
    pairs: dict[tuple[str, str], Pair] = {}
    skipped: list[dict[str, str]] = []
    problems: list[str] = []
    for path in files:
        items, bad = read_items(path)
        problems += bad
        for item in items:
            gold = item.get("gold_request") or {}
            o_text, d_text = gold.get("origin"), gold.get("destination")
            if not (isinstance(o_text, str) and o_text.strip() and isinstance(d_text, str) and d_text.strip()):
                missing = [k for k, v in (("origin", o_text), ("destination", d_text)) if not v]
                skipped.append({"item": item["id"], "reason": f"gold has no {' or '.join(missing)}"})
                continue
            o, d = store.geocode(o_text), store.geocode(d_text)
            unresolved = [t for t, p in ((o_text, o), (d_text, d)) if not p.resolved]
            if unresolved:
                skipped.append(
                    {"item": item["id"], "reason": "unresolved place: " + "; ".join(repr(t) for t in unresolved)}
                )
                continue
            if (o.lat, o.lon) == (d.lat, d.lon):
                skipped.append({"item": item["id"], "reason": "origin and destination are the same point"})
                continue
            point_pair = (o.lat, o.lon, d.lat, d.lon)  # one pair per pair of points, whatever the wording
            pair = pairs.get(point_pair)
            if pair is None:
                (o, o_names), (d, d_names) = _canonical(store, o), _canonical(store, d)
                pair = pairs[point_pair] = Pair(o, d, o_names, d_names)
            pair.item_ids.append(str(item["id"]))
    return sorted(pairs.values(), key=lambda p: p.key), skipped, problems


# --------------------------------------------------------------------------- fetch


class RateLimiter:
    """At most one call per `min_interval` seconds (across retries too)."""

    def __init__(self, min_interval: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.min_interval, self.clock, self.sleep = min_interval, clock, sleep
        self._last: float | None = None

    def wait(self) -> None:
        if self._last is not None:
            remaining = self.min_interval - (self.clock() - self._last)
            if remaining > 0:
                self.sleep(remaining)
        self._last = self.clock()


def _retryable(error: OSRMRouteError) -> bool:
    if isinstance(error, OSRMTemporaryError):
        return True
    response = getattr(error.__cause__, "response", None)
    return isinstance(error, OSRMResponseError) and getattr(response, "status_code", None) == 429


def fetch_route(router: OSRMRouter, pair: Pair, limiter: RateLimiter, retries: int, backoff: float,
                sleep: Callable[[float], None] = time.sleep) -> Route:
    """One OSRM route for the pair; retries temporary failures / HTTP 429. Raises OSRMRouteError."""
    for attempt in range(retries + 1):
        limiter.wait()
        try:
            return router.route(pair.origin, pair.destination)
        except OSRMRouteError as e:
            if attempt == retries or not _retryable(e):
                raise
            sleep(backoff * 2**attempt)
    raise AssertionError("unreachable")


def cached_payload(route: Route, pair: Pair, server: str) -> dict[str, Any]:
    """Route contract JSON with snapshot provenance (status cached) and a `_meta` block."""
    route = route.model_copy(update={"provenance": route.provenance.model_copy(update={"status": DataStatus.CACHED})})
    payload = json.loads(route.model_dump_json())
    meta = {
        "origin_text": pair.origin.text,
        "destination_text": pair.destination.text,
        "benchmark_items": pair.item_ids,
        "osrm_server": server,
        "built_by": "scripts/build_route_cache.py",
    }
    return {"_meta": meta, **payload}


def write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")  # compact: geometry is large
    tmp.replace(path)


# --------------------------------------------------------------------------- main


def build(
    args: argparse.Namespace,
    session: requests.Session | None = None,
    store: DataStore | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Collect pairs and (unless --dry-run) fetch and write them. `session`, `store` and `sleep`
    are injectable so tests run offline."""
    snapshot = repo_path(args.snapshot_dir)
    out = repo_path(args.out) if args.out else snapshot / "routes"
    store = store if store is not None else DataStore.from_dir(snapshot)
    files = sorted(repo_path(args.raw_dir).glob("*.jsonl")) + [Path(p) for p in args.extra_items]
    pairs, skipped_items, problems = collect_pairs(store, files)

    summary: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "dry_run": args.dry_run,
        "osrm_server": args.osrm_url,
        "snapshot_dir": str(snapshot),
        "out_dir": str(out),
        "item_files": [str(f) for f in files],
        "alias_files": not args.canonical_only,
        "pairs_total": len(pairs),
        "files_total": sum(len(p.filenames(args.canonical_only)) for p in pairs),
        "skipped_items": skipped_items,
        "file_problems": problems,
        "built": [],
        "skipped_existing": [],
        "failed": [],
    }
    if args.dry_run:
        summary["pairs"] = [
            {"origin": p.origin.text, "destination": p.destination.text, "items": p.item_ids,
             "files": p.filenames(args.canonical_only)}
            for p in pairs
        ]
        return summary

    out.mkdir(parents=True, exist_ok=True)
    router = OSRMRouter(args.osrm_url, http_session=session, timeout_seconds=args.timeout)
    limiter = RateLimiter(args.min_interval, sleep=sleep)
    for i, pair in enumerate(pairs, 1):
        names = pair.filenames(args.canonical_only)
        existing = [n for n in names if (out / n).is_file()]
        label = f"[{i}/{len(pairs)}] {pair.origin.text} -> {pair.destination.text}"
        if existing:
            source = json.loads((out / existing[0]).read_text(encoding="utf-8"))
            filled = [n for n in names if n not in existing]
            for n in filled:  # complete missing alias copies from the existing route, no network
                write_json(out / n, source)
            summary["skipped_existing"].append({"pair": list(pair.key), "existing": existing, "filled": filled})
            print(f"{label}: exists", file=sys.stderr)
            continue
        try:
            route = fetch_route(router, pair, limiter, args.retries, args.backoff, sleep=sleep)
        except OSRMRouteError as e:
            kind = "no_route" if isinstance(e, OSRMNoRouteError) else type(e).__name__
            summary["failed"].append({"pair": list(pair.key), "items": pair.item_ids, "reason": f"{kind}: {e}"})
            print(f"{label}: FAILED {kind}: {e}", file=sys.stderr)
            continue
        payload = cached_payload(route, pair, args.osrm_url)
        for n in names:
            write_json(out / n, payload)
        summary["built"].append({"pair": list(pair.key), "files": names, "distance_km": round(route.distance_km, 1),
                                 "duration_min": round(route.duration_min, 1)})
        print(f"{label}: {route.distance_km:.0f} km, {route.duration_min:.0f} min", file=sys.stderr)
    summary["finished_at"] = datetime.now(timezone.utc).isoformat()
    return summary


def print_summary(summary: dict[str, Any]) -> None:
    print(f"pairs total:     {summary['pairs_total']}  ({summary['files_total']} files"
          f"{' incl. aliases' if summary['alias_files'] else ''})")
    if summary["dry_run"]:
        print("dry run: no OSRM requests made")
        for p in summary["pairs"]:
            print(f"  {p['origin']} -> {p['destination']}  [{', '.join(p['items'])}]")
            for f in p["files"]:
                print(f"      {f}")
    else:
        print(f"built:           {len(summary['built'])}")
        print(f"skipped (exist): {len(summary['skipped_existing'])}")
        print(f"failed:          {len(summary['failed'])}")
        for f in summary["failed"]:
            print(f"  {f['pair'][0]} -> {f['pair'][1]}: {f['reason']}")
    print(f"items skipped:   {len(summary['skipped_items'])}")
    for s in summary["skipped_items"]:
        print(f"  {s['item']}: {s['reason']}")
    for p in summary["file_problems"]:
        print(f"  file problem: {p}")


def make_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python scripts/build_route_cache.py", description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="list pairs and file names; no OSRM requests")
    ap.add_argument("--raw-dir", default="benchmark/requests/raw", help="benchmark item files (*.jsonl)")
    ap.add_argument("--extra-items", nargs="*", default=[], help="more .jsonl files (e.g. not merged yet)")
    ap.add_argument("--snapshot-dir", default="data/snapshot")
    ap.add_argument("--out", help="default: <snapshot-dir>/routes")
    ap.add_argument("--osrm-url", default=DEFAULT_OSRM)
    ap.add_argument(
        "--min-interval", type=float, default=1.0, help="seconds between OSRM requests (>= 1 for the demo server)"
    )
    ap.add_argument("--retries", type=int, default=4)
    ap.add_argument("--backoff", type=float, default=2.0, help="first retry delay in seconds, doubling")
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--canonical-only", action="store_true", help="only the gold place form, no alias copies")
    ap.add_argument("--summary", help="summary JSON path (default: <snapshot-dir>/route_cache_report.json; "
                                      "dry run: printed only)")
    return ap


def main(argv: list[str] | None = None) -> None:
    ap = make_parser()
    args = ap.parse_args(argv)
    if args.min_interval < 1.0 and args.osrm_url.rstrip("/") == DEFAULT_OSRM:
        ap.error("the public OSRM demo server allows at most 1 request per second (--min-interval >= 1)")

    summary = build(args)
    print_summary(summary)
    if not args.dry_run:
        path = Path(args.summary) if args.summary else repo_path(args.snapshot_dir) / "route_cache_report.json"
        path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"summary: {path}")


if __name__ == "__main__":
    main()
