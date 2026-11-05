"""Build the real-data snapshot in the fixture layout. Owner: Aarya. Run on a machine with internet:

    python -m clickless.data.build_snapshot                 # whole US (needs NREL_API_KEY)
    python -m clickless.data.build_snapshot --states AZ,NV  # smaller trial run

Steps: AFDC stations -> OSM amenities around them -> benchmark place geocodes -> curated
vehicles -> write data/snapshot/{stations,amenities,vehicles,places}.json + routes/ -> load it
back through DataStore -> validate (blocking problems abort) -> MANIFEST.json with hashes,
counts, parameters and conversion reports. Downloads are cached under data/raw and
data/cache, so a rerun resumes instead of starting over (use --refresh-afdc to re-download).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import repo_path
from ..contracts import ChargerLevel, StationStatus
from . import afdc, geocode, osm_amenities
from .loaders import DataStore
from .snapshot import unaccepted_freeze_adrs, write_manifest
from .validate import blocking_problems, in_us, provenance_completeness, validate_store
from .vehicles import load_curated_vehicles


def _read_dotenv(path: Path) -> None:
    """Minimal .env reader (KEY=VALUE lines); never overrides variables already set."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _write_records(path: Path, records: list[Any], meta: dict[str, Any]) -> None:
    path.write_text(json.dumps({"_meta": meta, "records": records}))


def _latest_raw(raw_dir: Path, states: str | None) -> Path | None:
    tag = (states or "US").upper()
    files = sorted(raw_dir.glob(f"afdc_{tag}_*.json"))
    return files[-1] if files else None


def osm_targets(stations: list, scope: str) -> list:
    if scope == "none":
        return []
    usable = [s for s in stations if s.status == StationStatus.AVAILABLE and s.access == "public"]
    if scope == "all":
        return usable
    return [s for s in usable if any(p.level == ChargerLevel.DC_FAST for p in s.ports)]


def build(args: argparse.Namespace) -> int:
    if args.freeze and (pending := unaccepted_freeze_adrs()):
        print(f"Cannot freeze: these ADRs are not accepted yet: {pending}", file=sys.stderr)
        print("Build without --freeze for a draft snapshot, or get the ADRs approved first.", file=sys.stderr)
        return 2
    out = repo_path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "routes").mkdir(exist_ok=True)
    built_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    sources: dict[str, Any] = {}

    # 1. AFDC stations
    raw_dir = repo_path("data/raw/afdc")
    raw = None if args.refresh_afdc else (Path(args.afdc_raw) if args.afdc_raw else _latest_raw(raw_dir, args.states))
    if raw is None:
        _read_dotenv(repo_path(".env"))
        print("Downloading AFDC stations ...", flush=True)
        raw = Path(afdc.fetch_stations(os.environ.get("NREL_API_KEY", ""), state=args.states, out_dir=str(raw_dir)))
    else:
        print(f"Using saved AFDC download {raw}", flush=True)
    stations, report, meta = afdc.stations_from_raw(raw)
    outside = [s for s in stations if not in_us(s.lat, s.lon)]
    if outside:
        stations = [s for s in stations if in_us(s.lat, s.lon)]
        report.skipped["outside_us_bounding_boxes"] += len(outside)
    report.converted = len(stations)
    print(f"  stations: {len(stations)} kept, skipped {dict(report.skipped)}", flush=True)
    sources["afdc"] = {"raw_file": raw.name, **meta, "conversion": report.as_dict()}
    _write_records(
        out / "stations.json",
        [s.model_dump(mode="json") for s in stations],
        {"note": "AFDC public EV stations (ADR 0010)", "built_at": built_at},
    )

    # 2. OSM amenities
    targets = osm_targets(stations, args.osm_scope)
    if targets:
        print(f"OSM amenities around {len(targets)} stations (scope={args.osm_scope}) ...", flush=True)
        paths = osm_amenities.fetch_amenities(targets, radius_m=args.radius_m, batch_size=args.osm_batch_size)
        amenities, dropped, stamps = osm_amenities.amenities_from_raw(paths)
        window = [min(stamps), max(stamps)] if stamps else None
    else:
        amenities, dropped, window = [], {}, None
    print(f"  amenities: {len(amenities)}", flush=True)
    sources["osm"] = {
        "endpoint": osm_amenities.OVERPASS_URL,
        "scope": args.osm_scope,
        "stations_queried": len(targets),
        "radius_m": args.radius_m,
        "retrieved_window": window,
        "dropped": dict(dropped),
    }
    _write_records(
        out / "amenities.json",
        [a.model_dump(mode="json") for a in amenities],
        {"note": "OSM amenities near stations (ADR 0011)", "built_at": built_at},
    )

    # 3. places
    from evaluation.schema import load_items

    places = geocode.benchmark_places(load_items(repo_path("benchmark/requests/raw")))
    if args.skip_geocode:
        results: dict[str, dict[str, Any]] = {}
    else:
        print(f"Geocoding {len(places)} benchmark places ...", flush=True)
        results = geocode.geocode_places(places)
    geocode.write_places(results, out / "places.json")
    unresolved = sorted(p for p, r in results.items() if "error" in r)
    stamps = sorted(r["retrieved_at"] for r in results.values() if "retrieved_at" in r)
    sources["places"] = {
        "source": "OSM Nominatim",
        "endpoint": geocode.NOMINATIM_URL,
        "requested": len(places),
        "resolved": len(results) - len(unresolved),
        "unresolved": unresolved,
        "retrieved_window": [stamps[0], stamps[-1]] if stamps else None,
    }

    # 4. vehicles
    vehicles = load_curated_vehicles()
    _write_records(
        out / "vehicles.json",
        [v.model_dump(mode="json") for v in vehicles],
        {"note": "from data/curated/vehicles.csv (ADR 0009)", "built_at": built_at},
    )
    sources["vehicles"] = {"curated_csv": "data/curated/vehicles.csv", "count": len(vehicles)}

    # 5. load back + validate
    store = DataStore.from_dir(out)
    problems = validate_store(store)
    blocking = blocking_problems(problems)
    warnings = {k: len(v) for k, v in problems.items() if v and k not in blocking}
    completeness = provenance_completeness(store)
    print(f"Validation: blocking={ {k: len(v) for k, v in blocking.items()} } warnings={warnings}", flush=True)
    if blocking:
        for k, v in blocking.items():
            print(f"  {k}: {v[:5]}", file=sys.stderr)
        print("Snapshot NOT frozen: fix the blocking problems above.", file=sys.stderr)
        return 1

    manifest = write_manifest(
        str(out),
        extra={
            "built_at": built_at,
            "sources": sources,
            "validation": {"warnings": warnings, "provenance_completeness": completeness},
            "frozen": bool(args.freeze),
        },
    )
    kind = (
        "FROZEN snapshot"
        if args.freeze
        else "Draft snapshot (not frozen; rebuild with --freeze once ADRs are accepted)"
    )
    print(f"{kind} ready: {out} (manifest {manifest})")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default="data/snapshot")
    p.add_argument("--states", default=None, help="comma-separated state codes; default: whole US")
    p.add_argument("--afdc-raw", default=None, help="use this saved AFDC download instead of downloading")
    p.add_argument("--refresh-afdc", action="store_true", help="download AFDC again even if a saved file exists")
    p.add_argument("--osm-scope", choices=["dc_fast", "all", "none"], default="dc_fast")
    p.add_argument("--osm-batch-size", type=int, default=100)
    p.add_argument("--radius-m", type=int, default=400)
    p.add_argument("--skip-geocode", action="store_true")
    p.add_argument(
        "--freeze",
        action="store_true",
        help="mark the snapshot frozen; refused until ADR 0009, 0010 and 0011 are accepted",
    )
    return build(p.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
