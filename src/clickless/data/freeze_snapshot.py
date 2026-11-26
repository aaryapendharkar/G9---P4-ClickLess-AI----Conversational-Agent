"""Freeze the reviewed draft snapshot as it is, without rebuilding it. Owner: Aarya. (#36)

    python -m clickless.data.freeze_snapshot --dir data/snapshot --from-release snapshot-v1-draft

`build_snapshot --freeze` rebuilds everything from AFDC, OSM and Nominatim, which would replace the
draft the team has checked (stations, amenities, the place and route fixes of #103, #120, #154)
with new downloads. This step freezes the draft itself. Nothing is downloaded and no data file
changes; it refuses unless every check passes:

- the snapshot matches its MANIFEST.json (no exemption) and is not frozen yet;
- the ADRs that define a snapshot are accepted (snapshot.FREEZE_ADRS);
- the validator finds no blocking problem (snapshot_report.collect, the same check as the report);
- every file in routes/ is a valid Route listed in the manifest (add_routes.check_route_files);
- no stored place is a county/state match or a district mismatch (geocode, #120 and #122);
- every benchmark item that gives both places resolves them and finds its cached route.

Then MANIFEST.json gets `"frozen": true`, `frozen_at` and `frozen_from` (the release and the
SHA-256 of the draft's MANIFEST.json, so the frozen snapshot can be traced to the draft everyone
verified), and is re-hashed: the data files and their hashes stay byte-identical. add_places and
add_routes refuse a frozen snapshot, so it can't change afterwards. The `snapshot freeze` GitHub
Actions workflow runs this and publishes the result as a new release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from typing import Any

from ..config import repo_path
from . import geocode
from .add_routes import check_route_files
from .snapshot import unaccepted_freeze_adrs, verify_manifest, write_manifest


def freeze_problems(root, raw_dir: str = "benchmark/requests/raw") -> list[str]:
    """Every reason not to freeze the snapshot in `root` (empty = ready)."""
    manifest_path = root / "MANIFEST.json"
    if not manifest_path.is_file():
        return [f"no MANIFEST.json in {root}"]
    changed = verify_manifest(str(root))
    if changed:
        return [f"snapshot does not match its MANIFEST.json (missing or changed: {changed})"]
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    problems: list[str] = []
    if manifest.get("frozen"):
        problems.append("already frozen")
    pending = unaccepted_freeze_adrs()
    if pending:
        problems.append(f"ADRs not accepted: {pending}")

    from .snapshot_report import collect

    report = collect(root, raw_dir)
    if report.get("blocking"):
        problems.append(f"blocking validation problems: {report['blocking']}")

    routes, bad_routes = check_route_files(root / "routes")
    problems += [f"route file: {p}" for p in bad_routes]
    unlisted = sorted(
        f"routes/{p.name}" for p in (root / "routes").glob("*.json") if f"routes/{p.name}" not in manifest["files"]
    )
    if unlisted:
        problems.append(f"route files not in MANIFEST.json: {unlisted[:5]}{' ...' if len(unlisted) > 5 else ''}")

    places = json.loads((root / "places.json").read_text(encoding="utf-8"))
    details = (places.get("_meta") or {}).get("details", {}).get("places", {})
    bad_places = sorted(
        p for p, d in details.items()
        if geocode.looks_like_area_match(p, d) or geocode.looks_like_district_mismatch(p, d)
    )
    if bad_places:
        problems.append(f"places stored as a county/state or the wrong district: {bad_places}")

    problems += _unrouted_benchmark_items(root, raw_dir)
    return problems


def _unrouted_benchmark_items(root, raw_dir: str) -> list[str]:
    from evaluation.schema import load_items

    from ..config import load_config
    from ..planning.routing import CachedRouter
    from .loaders import DataStore

    store = DataStore.from_dir(root)
    router = CachedRouter(store, load_config("A")["energy"])
    missing = []
    for item in load_items(repo_path(raw_dir)):
        gold = item.gold_request
        if not gold.get("origin") or not gold.get("destination"):
            continue
        if {"origin", "destination"} & set(item.missing_safety_fields):
            continue  # left unresolved on purpose (clarification items)
        origin, destination = store.geocode(gold["origin"]), store.geocode(gold["destination"])
        if not (origin.resolved and destination.resolved):
            continue  # e.g. "Springfield": the benchmark expects a clarification, not a route
        try:
            router.route(origin, destination)
        except Exception as e:  # noqa: BLE001 - every missing route is a reason not to freeze
            missing.append(f"{item.id}: {type(e).__name__}")
    return [f"benchmark items without a cached route: {missing}"] if missing else []


def freeze(snapshot_dir: str = "data/snapshot", from_release: str | None = None,
           raw_dir: str = "benchmark/requests/raw") -> int:
    root = repo_path(snapshot_dir)
    problems = freeze_problems(root, raw_dir)
    if problems:
        print("Not frozen:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1
    manifest_path = root / "MANIFEST.json"
    draft_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    files_before = dict(manifest["files"])
    keep = {k: v for k, v in manifest.items() if k not in {"created_at", "files", "record_counts"}}
    write_manifest(str(root), extra={
        **keep,
        "frozen": True,
        "frozen_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "frozen_from": {"release": from_release, "manifest_sha256": draft_sha},
    })
    after = json.loads(manifest_path.read_text(encoding="utf-8"))
    if after["files"] != files_before:  # cannot happen when verify passed; never publish if it did
        print("file hashes changed while freezing; not frozen", file=sys.stderr)
        return 1
    print(f"FROZEN: {len(after['files'])} files, hashes unchanged; frozen from "
          f"{from_release or 'a local draft'} (MANIFEST sha256 {draft_sha[:12]})")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dir", default="data/snapshot")
    p.add_argument("--from-release", default=None, help="the release the draft was downloaded from")
    p.add_argument("--raw-dir", default="benchmark/requests/raw")
    p.add_argument("--check-only", action="store_true", help="list the reasons not to freeze, change nothing")
    args = p.parse_args(argv)
    if args.check_only:
        problems = freeze_problems(repo_path(args.dir), args.raw_dir)
        print("ready to freeze" if not problems else "\n".join(f"- {x}" for x in problems))
        return 0 if not problems else 1
    return freeze(args.dir, args.from_release, args.raw_dir)


if __name__ == "__main__":
    raise SystemExit(main())
