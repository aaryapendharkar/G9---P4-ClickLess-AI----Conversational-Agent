"""Report, verify and pack a built snapshot (issue #36). Owner: Aarya.

    python -m clickless.data.snapshot_report                  # Markdown report for the issue
    python -m clickless.data.snapshot_report --pack out.zip   # report + zip to share
    python -m clickless.data.snapshot_report --verify-only    # teammates, after downloading

The report re-checks everything instead of trusting the build log: manifest integrity
(`verify_manifest() == []`), a fresh validator run (blocking vs warning checks), counts
(stations, ports, amenities, vehicles, resolved places), the AFDC / OSM / geocoder retrieval
windows and parameters from the manifest, the frozen flag, and how many benchmark requests
have both endpoints resolved. Exit code 0 only when the manifest is intact and nothing blocks.
"""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from collections import Counter
from pathlib import Path
from typing import Any

from ..config import repo_path
from ..contracts import ChargerLevel
from .loaders import DataStore
from .snapshot import verify_manifest
from .validate import blocking_problems, validate_store


def _benchmark_coverage(store: DataStore, raw_dir: Path) -> dict[str, Any]:
    from evaluation.schema import load_items

    items = load_items(raw_dir)
    covered, unresolved, intentionally_missing = 0, [], 0
    for it in items:
        missing_place = [f for f in ("origin", "destination") if f in it.missing_safety_fields]
        if missing_place:
            intentionally_missing += 1
            continue
        ends = [it.gold_request.get(f) for f in ("origin", "destination")]
        if all(e and store.geocode(e).resolved for e in ends):
            covered += 1
        else:
            unresolved.append(it.id)
    return {
        "items": len(items),
        "both_endpoints_resolved": covered,
        "place_marked_missing_on_purpose": intentionally_missing,
        "unresolved_items": unresolved,
    }


def collect(
    snapshot_dir: str | Path = "data/snapshot", raw_dir: str | Path = "benchmark/requests/raw"
) -> dict[str, Any]:
    root = repo_path(snapshot_dir)
    manifest_path = root / "MANIFEST.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"no MANIFEST.json in {root}: build (make snapshot-build) or download the snapshot first"
        )
    manifest = json.loads(manifest_path.read_text())
    changed = verify_manifest(str(root))
    report: dict[str, Any] = {"dir": str(root), "verify_manifest": changed, "manifest": manifest}
    if changed:
        return report  # do not load data that does not match its manifest

    store = DataStore.from_dir(root)
    problems = validate_store(store)
    ports = [p for s in store.stations.values() for p in s.ports]
    by_level = Counter(p.level.value for p in ports)
    dc_unknown = sum(1 for p in ports if p.level == ChargerLevel.DC_FAST and p.power_kw is None)
    places = json.loads((root / "places.json").read_text())
    report.update(
        counts={
            "stations": len(store.stations),
            "port_groups": len(ports),
            "physical_ports": sum(p.count for p in ports),
            "port_groups_by_level": dict(sorted(by_level.items())),
            "dc_port_groups_with_unknown_power": dc_unknown,
            "amenities": len(store.amenities),
            "amenities_by_category": dict(sorted(Counter(a.category.value for a in store.amenities).items())),
            "vehicles": len(store.vehicles),
            "resolved_places": len(places["_meta"]["details"].get("places", {})),
            "gazetteer_keys": len(store.places),
        },
        blocking={k: len(v) for k, v in blocking_problems(problems).items()},
        warnings={k: len(v) for k, v in problems.items() if v and k not in blocking_problems(problems)},
        coverage=_benchmark_coverage(store, repo_path(raw_dir)),
    )
    return report


def to_markdown(r: dict[str, Any]) -> str:
    m = r["manifest"]
    src = m.get("sources", {})
    afdc, osm, places = src.get("afdc", {}), src.get("osm", {}), src.get("places", {})
    lines = [f"## Snapshot report ({'FROZEN' if m.get('frozen') else 'draft, not frozen'})", ""]
    lines.append(f"- Built: {m.get('built_at', '?')} · manifest created {m.get('created_at', '?')}")
    ok = not r["verify_manifest"]
    lines.append(f"- `verify_manifest()` → `{r['verify_manifest']}` ({'intact' if ok else 'MISMATCH: do not use'})")
    if not ok:
        return "\n".join(lines) + "\n"
    c, cov = r["counts"], r["coverage"]
    scope = afdc.get("params", {}).get("state") or "whole US"
    lines += [
        f"- Blocking validation problems: {r['blocking'] or 'none'}",
        f"- Warnings: {r['warnings'] or 'none'}",
        "",
        "| Source | Retrieved | Parameters |",
        "|---|---|---|",
        f"| AFDC | {afdc.get('retrieved_at', '?')} | {json.dumps(afdc.get('params', {}))} |",
        f"| OSM Overpass | {' → '.join(osm.get('retrieved_window') or ['—'])} | scope={osm.get('scope')}, "
        f"radius_m={osm.get('radius_m')}, stations_queried={osm.get('stations_queried')} |",
        f"| Nominatim | {' → '.join(places.get('retrieved_window') or ['—'])} | "
        f"requested={places.get('requested')}, unresolved={places.get('unresolved')} |",
        "",
        "| Count | Value |",
        "|---|---|",
        f"| stations | {c['stations']} |",
        f"| ports (groups / physical) | {c['port_groups']} / {c['physical_ports']} |",
        f"| port groups by level | {c['port_groups_by_level']} |",
        f"| DC port groups with unknown power | {c['dc_port_groups_with_unknown_power']} |",
        f"| amenities | {c['amenities']} {c['amenities_by_category']} |",
        f"| vehicles | {c['vehicles']} |",
        f"| resolved places | {c['resolved_places']} ({c['gazetteer_keys']} lookup keys) |",
        "",
        f"- AFDC skipped records: {afdc.get('conversion', {}).get('skipped', {})}",
        f"- Station scope: **{scope}**. Benchmark: {cov['both_endpoints_resolved']} of {cov['items']} requests have "
        f"both endpoints resolved; {cov['place_marked_missing_on_purpose']} leave a place unresolved on purpose; "
        f"unresolved otherwise: {cov['unresolved_items'] or 'none'}.",
    ]
    return "\n".join(lines) + "\n"


def pack(snapshot_dir: str | Path, out_zip: str | Path) -> Path:
    """Zip MANIFEST.json, every file it lists, and an empty routes/ folder."""
    root = repo_path(snapshot_dir)
    manifest = json.loads((root / "MANIFEST.json").read_text())
    out = Path(out_zip)
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.write(root / "MANIFEST.json", "MANIFEST.json")
        for rel in sorted(manifest["files"]):
            z.write(root / rel, rel)
        if not any(rel.startswith("routes/") for rel in manifest["files"]):
            z.writestr("routes/", "")
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dir", default="data/snapshot")
    p.add_argument("--pack", default=None, help="also write a zip to share (team drive / GitHub release)")
    p.add_argument("--out", default=None, help="also save the Markdown report to this file")
    p.add_argument("--verify-only", action="store_true", help="only check files against MANIFEST.json")
    args = p.parse_args(argv)
    root = repo_path(args.dir)
    if args.verify_only:
        if not (root / "MANIFEST.json").is_file():
            print(f"no MANIFEST.json in {root}", file=sys.stderr)
            return 1
        changed = verify_manifest(str(root))
        print("verify_manifest() ->", changed, "(intact)" if not changed else "(MISMATCH: re-download these files)")
        return 0 if not changed else 1
    report = collect(root)
    text = to_markdown(report)
    print(text)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text)
    if report["verify_manifest"] or report["blocking"]:
        return 1
    if args.pack:
        print(f"Packed: {pack(root, args.pack)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
