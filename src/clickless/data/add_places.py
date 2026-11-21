"""Geocode benchmark places a snapshot doesn't have yet and record them in MANIFEST.json. Owner: Aarya. (#103)

    python -m clickless.data.add_places --dir data/snapshot

New benchmark items (e.g. #102) can name places the snapshot's closed gazetteer (places.json)
lacks, so they can't be routed or evaluated. Rebuilding the whole snapshot for that would also
re-download stations and amenities. This step instead:

- refuses a frozen snapshot or one whose files no longer match MANIFEST.json;
- keeps every place already in places.json exactly as it is (coordinates, OSM id, retrieval time),
  and its unresolved places too (except the county matches below, when asked to fix them);
- geocodes only the benchmark places (benchmark/requests/raw) it doesn't know yet, with the same
  rules as the full build (data/geocode.py: Nominatim, places that name a US state only);
- with --fix-area-matches, also re-geocodes stored places that resolved to a county or state
  instead of the town (e.g. "Elko, NV" -> Elko County, 54 km off; see geocode.py) and removes the
  route files that start or end at their old points, listing each in `sources.places.routes_removed`;
- rewrites places.json through geocode.write_places, so the lookup keys follow the usual rules
  (a bare city name is kept only while exactly one place has it), updates `sources.places` and
  re-hashes the snapshot. Nothing new to geocode: nothing changes.

Run `scripts/build_route_cache.py` afterwards; it only fetches the pairs that have no route yet.
The `snapshot routes` GitHub Actions workflow runs both steps and republishes the release.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
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
    fix_area_matches: bool = False,
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
    payload = json.loads(places_path.read_text(encoding="utf-8"))
    known = known_places(payload)
    wanted = geocode.benchmark_places(load_items(repo_path(benchmark_dir)))
    new = [p for p in wanted if p not in known]
    refresh = sorted(p for p, r in known.items() if fix_area_matches and geocode.looks_like_area_match(p, r))
    if not new and not refresh:
        print(f"Places: all {len(wanted)} benchmark places are already in places.json; nothing to add")
        return 0

    if refresh:
        print(f"Re-geocoding {len(refresh)} place(s) stored as a county or state instead of the town:", flush=True)
        for place in refresh:
            print(f"  {place} (was: {known[place].get('display_name')})", flush=True)
    if new:
        print(f"Geocoding {len(new)} new place(s):", flush=True)
        for place in new:
            print(f"  {place}", flush=True)
    fresh = geocode.geocode_places(new + refresh, cache_dir, endpoint=endpoint, pause_s=pause_s)
    results = {**known, **fresh}
    geocode.write_places(results, places_path)

    # Routes to or from a re-geocoded place start or end at the old (wrong) point: remove them, so the
    # route builder rebuilds those pairs. This is the only way a places/routes run removes a route, and
    # every removed file is listed in sources.places.routes_removed.
    old_records = payload.get("records") or {}
    stale_points = {tuple(old_records[p.lower()]) for p in refresh if p.lower() in old_records}
    stale_keys = {k for k, v in old_records.items() if tuple(v) in stale_points}
    removed = remove_routes_touching(root / "routes", stale_keys)

    resolved = {p: r for p, r in results.items() if "error" not in r}
    unresolved = {p: r["error"] for p, r in results.items() if "error" in r}
    stamps = sorted(r["retrieved_at"] for r in resolved.values() if r.get("retrieved_at"))
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
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
    }
    if new:
        sources["places"].update(added=sorted(new), added_at=now)
    if refresh:
        sources["places"]["refreshed"] = [*previous.get("refreshed", []), *(
            {"place": p, "was": known[p].get("display_name"), "was_at": [known[p].get("lat"), known[p].get("lon")],
             "now": fresh[p].get("display_name", fresh[p].get("error")),
             "now_at": [fresh[p].get("lat"), fresh[p].get("lon")], "at": now}
            for p in refresh
        )]
        sources["places"]["routes_removed"] = [*previous.get("routes_removed", []), *removed]
    keep = {k: v for k, v in manifest.items() if k not in {"created_at", "files", "record_counts"}}
    write_manifest(str(root), extra={**keep, "sources": sources, "frozen": False})

    if new:
        added_ok = [p for p in new if "error" not in fresh[p]]
        print(f"Places added: {len(added_ok)} of {len(new)} new; places.json now has {len(resolved)} places")
        for p in new:
            if "error" in fresh[p]:
                print(f"  not resolved: {p} ({fresh[p]['error']})")
    for p in refresh:
        f = fresh[p]
        if "error" in f:
            print(f"  re-geocoded {p}: now unresolved ({f['error']})")
        else:
            moved = haversine_km((known[p]["lat"], known[p]["lon"]), (f["lat"], f["lon"]))
            print(f"  re-geocoded {p}: moved {moved:.0f} km to {f['display_name']}")
    if refresh:
        print(f"Removed {len(removed)} route file(s) that used the old points; build_route_cache.py rebuilds them")
    return 0


def remove_routes_touching(routes_dir: Path, keys: set[str]) -> list[str]:
    """Delete route files whose origin or destination text is one of `keys` (lowercase gazetteer keys)."""
    removed = []
    for path in sorted(routes_dir.glob("*.json")) if keys and routes_dir.is_dir() else []:
        meta = json.loads(path.read_text(encoding="utf-8")).get("_meta") or {}
        ends = {str(meta.get("origin_text", "")).lower(), str(meta.get("destination_text", "")).lower()}
        if ends & keys:
            path.unlink()
            removed.append(f"routes/{path.name}")
    return removed


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dir", default="data/snapshot")
    p.add_argument("--benchmark-dir", default="benchmark/requests/raw")
    p.add_argument("--cache-dir", default="data/cache/geocode")
    p.add_argument("--fix-area-matches", action="store_true",
                   help="re-geocode stored places that resolved to a county or state, and remove their routes")
    args = p.parse_args(argv)
    return add_places(args.dir, args.benchmark_dir, args.cache_dir, fix_area_matches=args.fix_area_matches)


if __name__ == "__main__":
    raise SystemExit(main())
