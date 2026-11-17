"""Add OSM amenities to an existing stations-only snapshot. Owner: Aarya. (Issue #36, ADR 0011)

    python -m clickless.data.add_amenities --dir data/snapshot

Use this when stations, vehicles and places are already built (e.g. the published
`snapshot-v1-draft-stations` release, built with `build_snapshot --osm-scope none`) and only the
OpenStreetMap amenities are missing. It does not touch stations.json, vehicles.json or
places.json; it writes amenities.json, records the OSM source in MANIFEST.json and re-hashes.

Overpass servers are tried in order (main server, then the public mirror by default). Batches
already saved under data/raw/osm are reused whichever server answered them, so a stopped run
resumes. The GitHub Actions workflow `snapshot-amenities` runs this on GitHub's servers.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import repo_path
from . import osm_amenities
from .build_snapshot import _write_records, osm_targets
from .loaders import DataStore
from .snapshot import verify_manifest, write_manifest
from .validate import blocking_problems, provenance_completeness, validate_store

DEFAULT_SERVERS = (osm_amenities.OVERPASS_URL, osm_amenities.OVERPASS_MIRROR_URL)


def add_amenities(
    snapshot_dir: str = "data/snapshot",
    *,
    servers: tuple[str, ...] | list[str] = DEFAULT_SERVERS,
    scope: str = "dc_fast",
    radius_m: int = 400,
    batch_size: int = 100,
    raw_dir: str = "data/raw/osm",
    from_raw: list[str] | None = None,
) -> int:
    """from_raw: use these saved element files (e.g. extract_*.json from clickless.data.osm_extract)
    instead of querying Overpass."""
    root = repo_path(snapshot_dir)
    manifest_path = root / "MANIFEST.json"
    if not manifest_path.is_file():
        print(f"no MANIFEST.json in {root}: unzip the stations-only snapshot there first", file=sys.stderr)
        return 1
    changed = verify_manifest(str(root))
    if changed:
        print(f"snapshot does not match its MANIFEST.json (missing or changed: {changed})", file=sys.stderr)
        return 1
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("frozen"):
        print("refusing to change a frozen snapshot; build a new one instead", file=sys.stderr)
        return 1

    store = DataStore.from_dir(root)
    targets = osm_targets(list(store.stations.values()), scope)
    print(f"OSM amenities around {len(targets)} stations (scope={scope}) ...", flush=True)

    paths: list[str] | None = None
    used: list[str] = []
    extract_sources: list[dict[str, Any]] = []
    if from_raw is not None:
        paths = sorted(str(p) for p in from_raw)
        if not paths:
            print("no extract files given", file=sys.stderr)
            return 1
        for path in paths:
            meta = json.loads(Path(path).read_text(encoding="utf-8"))["_meta"]
            if meta.get("radius_m") != radius_m or meta.get("scope") != scope:
                print(f"{path}: radius/scope {meta.get('radius_m')}/{meta.get('scope')} "
                      f"!= {radius_m}/{scope}", file=sys.stderr)
                return 1
            extract_sources.append({k: meta.get(k) for k in ("endpoint", "retrieved_at", "match_stats")})
        servers = []
    for i, server in enumerate(servers):
        used.append(server)
        print(f"  Overpass server: {server}", flush=True)
        try:
            paths = osm_amenities.fetch_amenities(
                targets, raw_dir, radius_m=radius_m, batch_size=batch_size, endpoint=server
            )
            break
        except osm_amenities.OverpassError as e:
            more = i + 1 < len(servers)
            print(f"  {e}" + ("\n  switching to the next server ..." if more else ""), flush=True)
    if paths is None:
        print("Every Overpass server failed. Saved batches are kept: run the same command again.", file=sys.stderr)
        return 1

    amenities, dropped, stamps = osm_amenities.amenities_from_raw(paths)
    added_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    print(f"  amenities: {len(amenities)}", flush=True)
    _write_records(
        root / "amenities.json",
        [a.model_dump(mode="json") for a in amenities],
        {"note": "OSM amenities near stations (ADR 0011)", "built_at": added_at},
    )

    store = DataStore.from_dir(root)
    problems = validate_store(store)
    blocking = blocking_problems(problems)
    warnings = {k: len(v) for k, v in problems.items() if v and k not in blocking}
    print(f"Validation: blocking={ {k: len(v) for k, v in blocking.items()} } warnings={warnings}", flush=True)
    if blocking:
        for k, v in blocking.items():
            print(f"  {k}: {v[:5]}", file=sys.stderr)
        return 1

    sources = dict(manifest.get("sources") or {})
    sources["osm"] = {
        "endpoint": used[-1] if used else "geofabrik extracts",
        "servers_tried": used,  # each batch file records the server that answered it
        "extracts": extract_sources,
        "scope": scope,
        "stations_queried": len(targets),
        "radius_m": radius_m,
        "retrieved_window": [min(stamps), max(stamps)] if stamps else None,
        "dropped": dict(dropped),
        "added_to_existing_snapshot_at": added_at,
    }
    keep = {k: v for k, v in manifest.items() if k not in {"created_at", "files", "record_counts"}}
    write_manifest(
        str(root),
        extra={
            **keep,
            "sources": sources,
            "validation": {"warnings": warnings, "provenance_completeness": provenance_completeness(store)},
            "frozen": False,
        },
    )
    print(f"Draft snapshot with amenities ready: {root}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dir", default="data/snapshot")
    p.add_argument(
        "--overpass-url",
        action="append",
        default=None,
        help="Overpass server to use; repeat to give fallbacks in order "
        f"(default: {' then '.join(DEFAULT_SERVERS)})",
    )
    p.add_argument("--osm-scope", choices=["dc_fast", "all"], default="dc_fast")
    p.add_argument("--radius-m", type=int, default=400)
    p.add_argument("--osm-batch-size", type=int, default=100)
    p.add_argument("--raw-dir", default="data/raw/osm")
    p.add_argument(
        "--from-raw",
        nargs="+",
        default=None,
        help="use these saved element files (from clickless.data.osm_extract match) instead of Overpass",
    )
    args = p.parse_args(argv)
    return add_amenities(
        args.dir,
        servers=args.overpass_url or DEFAULT_SERVERS,
        scope=args.osm_scope,
        radius_m=args.radius_m,
        batch_size=args.osm_batch_size,
        raw_dir=args.raw_dir,
        from_raw=args.from_raw,
    )


if __name__ == "__main__":
    raise SystemExit(main())
