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


def write_manifest(snapshot_dir: str = "data/snapshot") -> Path:
    root = repo_path(snapshot_dir)
    files = sorted(p for p in root.rglob("*") if p.is_file() and p.name != "MANIFEST.json")
    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": {str(p.relative_to(root)): sha256_file(p) for p in files},
        # TODO(Aarya): record counts per file, source retrieval windows, AFDC/OSM query params
    }
    out = root / "MANIFEST.json"
    out.write_text(json.dumps(manifest, indent=2))
    return out


def verify_manifest(snapshot_dir: str = "data/snapshot") -> list[str]:
    """Return files whose hash no longer matches the manifest (empty list = intact)."""
    root = repo_path(snapshot_dir)
    manifest = json.loads((root / "MANIFEST.json").read_text())
    return [rel for rel, digest in manifest["files"].items() if sha256_file(root / rel) != digest]
