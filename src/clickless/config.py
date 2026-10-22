"""Config loading: `load_config("A")` -> configs/config_a.yaml with its `includes` merged in."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]


def repo_path(rel: str | Path) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else REPO_ROOT / p


def load_config(name_or_path: str) -> dict[str, Any]:
    if name_or_path.upper() in {"A", "B", "C"}:
        path = repo_path(f"configs/config_{name_or_path.lower()}.yaml")
    else:
        path = repo_path(name_or_path)
    with open(path) as f:
        cfg = yaml.safe_load(f)
    for key, rel in (cfg.pop("includes", None) or {}).items():
        with open(repo_path(rel)) as f:
            cfg[key] = yaml.safe_load(f)
    return cfg
