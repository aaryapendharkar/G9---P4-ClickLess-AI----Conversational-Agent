"""Which data a run used: the fixtures, or a snapshot identified by the hash of its MANIFEST.json.

Shared by the harness (recorded in every result's run_metadata, in the process that actually
ran the config) and the report (one report must not mix snapshots). Imports no system code.
Owner: Ian.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from typing import Any

from clickless.config import repo_path


def snapshot_info(cfg: Mapping[str, Any], env: Mapping[str, str] | None = None) -> dict[str, str | None]:
    """{"snapshot_id", "snapshot_manifest_sha256"} for this config's data source.

    snapshot_id is 'fixtures' (or the configured non-snapshot source), or
    'snapshot:<first 12 hex of sha256(MANIFEST.json)> (<draft|frozen>, created <ts>)'.
    CLICKLESS_DATA_SOURCE overrides data.source, as in DataStore.from_config.
    """
    env = os.environ if env is None else env
    data = cfg.get("data", {}) or {}
    source = env.get("CLICKLESS_DATA_SOURCE", "").strip().lower() or data.get("source", "fixtures")
    if source != "snapshot":
        return {"snapshot_id": source, "snapshot_manifest_sha256": None}
    manifest = repo_path(data.get("snapshot_dir", "data/snapshot")) / "MANIFEST.json"
    if not manifest.is_file():
        return {"snapshot_id": "snapshot (no MANIFEST.json)", "snapshot_manifest_sha256": None}
    raw = manifest.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    meta = json.loads(raw)
    kind = "frozen" if meta.get("frozen") else "draft"
    return {
        "snapshot_id": f"snapshot:{digest[:12]} ({kind}, created {meta.get('created_at', '?')})",
        "snapshot_manifest_sha256": digest,
    }
