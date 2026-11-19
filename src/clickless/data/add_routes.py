"""Add a built route cache to an existing snapshot and record it in MANIFEST.json. Owner: Aarya. (#36)

    python scripts/build_route_cache.py --snapshot-dir data/snapshot     # Ian's builder, OSRM
    python -m clickless.data.add_routes --dir data/snapshot

`build_route_cache.py` writes `routes/<origin>__<destination>.json` (the names CachedRouter reads)
and `route_cache_report.json`, but does not touch MANIFEST.json, so on their own those files are
neither hashed nor checked. This step checks every route file and re-hashes the snapshot:

- the snapshot's existing files still match MANIFEST.json, and the snapshot is not frozen;
- every file in routes/ is named `<slug>__<slug>.json` and loads as the `Route` contract
  (its `_meta` block is ignored, as CachedRouter does);
- `sources.routes` records the number of routes and pairs, the OSRM server, the retrieval window
  and the builder's failures; everything else in the manifest is kept.

The GitHub Actions workflow `snapshot routes` runs both steps and republishes the release.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from ..config import repo_path
from ..contracts import Route
from .snapshot import verify_manifest, write_manifest

_ROUTE_FILE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*__[a-z0-9]+(?:-[a-z0-9]+)*\.json$")
REPORT_FILE = "route_cache_report.json"


def check_route_files(routes_dir) -> tuple[list[Route], list[str]]:
    """(valid routes, problems). A problem names the file and what is wrong with it."""
    routes, problems = [], []
    for path in sorted(routes_dir.iterdir()) if routes_dir.is_dir() else []:
        if path.is_dir() or path.name.endswith(".tmp"):
            problems.append(f"{path.name}: unexpected entry (leftover temporary file or folder)")
            continue
        if not _ROUTE_FILE.match(path.name):
            problems.append(f"{path.name}: not named <origin>__<destination>.json with CachedRouter slugs")
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload.pop("_meta", None)
            routes.append(Route.model_validate(payload))
        except Exception as e:  # noqa: BLE001 - report every broken file, not just the first
            problems.append(f"{path.name}: {type(e).__name__}: {str(e).splitlines()[0][:200]}")
    return routes, problems


def add_routes(snapshot_dir: str = "data/snapshot") -> int:
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

    routes, problems = check_route_files(root / "routes")
    if problems:
        print(f"{len(problems)} bad route file(s):", file=sys.stderr)
        for p in problems[:20]:
            print(f"  {p}", file=sys.stderr)
        return 1
    if not routes:
        print(f"no route files in {root / 'routes'}: run scripts/build_route_cache.py first", file=sys.stderr)
        return 1

    report: dict[str, Any] = {}
    report_path = root / REPORT_FILE
    if report_path.is_file():
        report = json.loads(report_path.read_text(encoding="utf-8"))
    stamps = sorted(r.provenance.retrieved_at for r in routes)
    distinct = {r.route_id for r in routes}
    sources = dict(manifest.get("sources") or {})
    sources["routes"] = {
        "files": len(routes),
        "distinct_routes": len(distinct),  # alias copies share one route_id
        "sources": dict(Counter(r.provenance.source for r in routes)),
        "osrm_server": report.get("osrm_server"),
        "pairs_total": report.get("pairs_total"),
        "pairs_failed": [f.get("pair") for f in report.get("failed", [])],
        "items_skipped": len(report.get("skipped_items", [])),
        "alias_files": report.get("alias_files"),
        "retrieved_window": [stamps[0].isoformat(), stamps[-1].isoformat()],
        "added_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "builder": "scripts/build_route_cache.py",
    }
    keep = {k: v for k, v in manifest.items() if k not in {"created_at", "files", "record_counts"}}
    write_manifest(str(root), extra={**keep, "sources": sources, "frozen": False})
    failed = len(sources["routes"]["pairs_failed"])
    print(f"Routes added: {len(routes)} files, {len(distinct)} distinct routes"
          + (f", {failed} pair(s) failed in the builder (see {REPORT_FILE})" if failed else ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dir", default="data/snapshot")
    return add_routes(p.parse_args(argv).dir)


if __name__ == "__main__":
    raise SystemExit(main())
