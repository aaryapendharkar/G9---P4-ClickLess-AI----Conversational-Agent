"""Geocode benchmark places a snapshot doesn't have yet and record them in MANIFEST.json. Owner: Aarya. (#103)

    python -m clickless.data.add_places --dir data/snapshot

New benchmark items (e.g. #102) can name places the snapshot's closed gazetteer (places.json)
lacks, so they can't be routed or evaluated. Rebuilding the whole snapshot for that would also
re-download stations and amenities. This step instead:

- refuses a frozen snapshot or one whose files no longer match MANIFEST.json;
- keeps every place already in places.json exactly as it is (coordinates, OSM id, retrieval time),
  and its unresolved places too;
- geocodes only the benchmark places (benchmark/requests/raw) it doesn't know yet, with the same
  rules as the full build (data/geocode.py: Nominatim, places that name a US state only);
- rewrites places.json through geocode.write_places, so the lookup keys follow the usual rules
  (a bare city name is kept only while exactly one place has it), updates `sources.places` and
  re-hashes the snapshot. Nothing new to geocode: nothing changes.

Run `scripts/build_route_cache.py` afterwards; it only fetches the pairs that have no route yet.
The `snapshot routes` GitHub Actions workflow runs both steps and republishes the release.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from typing import Any

from ..config import repo_path
from . import geocode
from .snapshot import verify_manifest, write_manifest


def known_places(places_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """place -> geocode result already in places.json (resolved ones and {"error": ...} ones)."""
    details = (places_payload.get("_meta") or {}).get("details") or {}
    known = {p: dict(r) for p, r in (details.get("places") or {}).items()}
    known.update({p: {"error": e} for p, e in (details.get("unresolved") or {}).items()})
    return known


def add_places(
    snapshot_dir: str = "data/snapshot",
    benchmark_dir: str = "benchmark/requests/raw",
    cache_dir: str = "data/cache/geocode",
    *,
    endpoint: str = geocode.NOMINATIM_URL,
    pause_s: float = 1.1,
) -> int:
    from evaluation.schema import load_items

    root = repo_path(snapshot_dir)
    manifest_path = root / "MANIFEST.json"
    if not manifest_path.is_file():
        print(f"no MANIFEST.json in {root}", file=sys.stderr)
        return 1
    changed = verify_manifest(str(root))
    if changed:
        print(f"snapshot does not match its MANIFEST.json (missing or changed: {changed})", file=sys.stderr)
        return 1
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("frozen"):
        print("refusing to change a frozen snapshot; build a new one instead", file=sys.stderr)
        return 1

    places_path = root / "places.json"
    known = known_places(json.loads(places_path.read_text(encoding="utf-8")))
    wanted = geocode.benchmark_places(load_items(repo_path(benchmark_dir)))
    new = [p for p in wanted if p not in known]
    if not new:
        print(f"Places: all {len(wanted)} benchmark places are already in places.json; nothing to add")
        return 0

    print(f"Geocoding {len(new)} new place(s) ...", flush=True)
    fresh = geocode.geocode_places(new, cache_dir, endpoint=endpoint, pause_s=pause_s)
    results = {**known, **fresh}
    geocode.write_places(results, places_path)

    resolved = {p: r for p, r in results.items() if "error" not in r}
    unresolved = {p: r["error"] for p, r in results.items() if "error" in r}
    stamps = sorted(r["retrieved_at"] for r in resolved.values() if r.get("retrieved_at"))
    sources = dict(manifest.get("sources") or {})
    previous = dict(sources.get("places") or {})
    sources["places"] = {
        **previous,
        "source": "OSM Nominatim",
        "endpoint": endpoint,
        "requested": len(results),
        "resolved": len(resolved),
        "unresolved": sorted(unresolved),
        "retrieved_window": [stamps[0], stamps[-1]] if stamps else None,
        "added": sorted(new),
        "added_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    keep = {k: v for k, v in manifest.items() if k not in {"created_at", "files", "record_counts"}}
    write_manifest(str(root), extra={**keep, "sources": sources, "frozen": False})

    added_ok = [p for p in new if "error" not in fresh[p]]
    print(f"Places added: {len(added_ok)} of {len(new)} new; places.json now has {len(resolved)} places")
    for p in new:
        if "error" in fresh[p]:
            print(f"  not resolved: {p} ({fresh[p]['error']})")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dir", default="data/snapshot")
    p.add_argument("--benchmark-dir", default="benchmark/requests/raw")
    p.add_argument("--cache-dir", default="data/cache/geocode")
    args = p.parse_args(argv)
    return add_places(args.dir, args.benchmark_dir, args.cache_dir)


if __name__ == "__main__":
    raise SystemExit(main())
