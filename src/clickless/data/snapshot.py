"""Freeze a snapshot directory and write MANIFEST.json (hash per file). Owner: Aarya."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from ..config import repo_path


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _record_count(path: Path) -> int | None:
    if path.suffix != ".json":
        return None
    try:
        records = json.loads(path.read_text(encoding="utf-8")).get("records")
    except (ValueError, AttributeError):
        return None
    return len(records) if isinstance(records, (list, dict)) else None


def write_manifest(snapshot_dir: str = "data/snapshot", extra: dict | None = None) -> Path:
    """Hash every file; also record per-file record counts and `extra` (sources, query
    parameters, retrieval windows, conversion reports -- see data/build_snapshot.py)."""
    root = repo_path(snapshot_dir)
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.name != "MANIFEST.json")
    counts = {str(p.relative_to(root)): n for p in files if (n := _record_count(p)) is not None}
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": {str(p.relative_to(root)): sha256_file(p) for p in files},
        "record_counts": counts,
        **(extra or {}),
    }
    out = root / "MANIFEST.json"
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return out


def verify_manifest(snapshot_dir: str = "data/snapshot") -> list[str]:
    """Return files that are missing or whose hash no longer matches the manifest (empty = intact)."""
    root = repo_path(snapshot_dir)
    manifest = json.loads((root / "MANIFEST.json").read_text(encoding="utf-8"))
    return [
        rel
        for rel, digest in manifest["files"].items()
        if not (root / rel).is_file() or sha256_file(root / rel) != digest
    ]


# ADRs that define what a snapshot contains; all must be accepted before a freeze (issue #32).
FREEZE_ADRS: tuple[str, ...] = (
    "0009-vehicle-table-third-party-sources.md",
    "0010-afdc-station-normalization.md",
    "0011-osm-amenity-mapping.md",
)


def adr_status(path: Path) -> str:
    """The text after '- Status:' in an ADR, lowercased ('' if absent)."""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().lower().startswith("- status:"):
            return line.split(":", 1)[1].strip().lower()
    return ""


def unaccepted_freeze_adrs(decisions_dir: str = "docs/decisions") -> dict[str, str]:
    """{adr file: status} for every freeze ADR that is missing or not accepted yet."""
    root = repo_path(decisions_dir)
    out = {}
    for name in FREEZE_ADRS:
        p = root / name
        status = adr_status(p) if p.exists() else "missing"
        if not status.startswith("accepted"):
            out[name] = status or "no status line"
    return out
