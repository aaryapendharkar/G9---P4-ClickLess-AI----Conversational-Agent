"""Config names for evaluation runs, and the data-source guard for benchmark splits (#119). Owner: Ian.

Shared by the harness and the report. It imports no system code beyond config loading, so
`evaluation.report` can check every config before it runs anything (the report stays independent of
the pipeline).

* config_path / load_named_config: "A", "B", "C" (any case) and the named ablations (ABLATIONS, e.g.
  "C-noKG") -> their YAML under configs/; anything else is a path.
* require_snapshot: a benchmark split (dev, test, raw) holds real places and curated vehicles that exist
  only in the snapshot. On the synthetic fixture world every item comes back rejected_incomplete and
  the run still writes a report full of zeros (#119), so a split run on fixtures is refused before any
  item runs. Explicit --items runs (e.g. benchmark/requests/dev/fixture_smoke.jsonl) are not affected.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from clickless.config import load_config, repo_path

# Named ablations: config name (as in results files and the report) -> config file.
ABLATIONS = {"C-noKG": "configs/config_c_nokg.yaml"}

SNAPSHOT_HINT = "Evaluation needs the snapshot: set CLICKLESS_DATA_SOURCE=snapshot (see README)"


class SnapshotRequired(ValueError):
    """A benchmark split was requested on the synthetic fixture world (#119)."""


def config_path(config: str) -> Path:
    if config.upper() in {"A", "B", "C"}:
        return repo_path(f"configs/config_{config.lower()}.yaml")
    if config in ABLATIONS:
        return repo_path(ABLATIONS[config])
    return repo_path(config)


def load_named_config(config: str) -> dict[str, Any]:
    return load_config(str(config_path(config)))


def data_source(cfg: Mapping[str, Any]) -> str:
    """'fixtures' or 'snapshot', with the same CLICKLESS_DATA_SOURCE override as DataStore."""
    return os.environ.get("CLICKLESS_DATA_SOURCE", "").strip().lower() or cfg["data"]["source"]


def require_snapshot(config: str, cfg: Mapping[str, Any], split: str, items_paths: Sequence[str] | None) -> None:
    """Raise SnapshotRequired if `split` (not explicit items) would run on the fixture world."""
    if items_paths:
        return
    source = data_source(cfg)
    if source == "snapshot":
        return
    origin = "CLICKLESS_DATA_SOURCE" if os.environ.get("CLICKLESS_DATA_SOURCE", "").strip() else "data.source"
    raise SnapshotRequired(
        f"{SNAPSHOT_HINT}. Config {config} runs on {source!r} ({origin}), but the {split!r} split uses real "
        "places and vehicles that exist only in the snapshot, so every item would come back "
        "rejected_incomplete. Verify the snapshot with `make snapshot-verify`. For a fixture smoke run, pass "
        "--items benchmark/requests/dev/fixture_smoke.jsonl instead of a split."
    )
