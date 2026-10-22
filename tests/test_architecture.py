"""Architecture rules that keep the team decoupled and the evaluation honest."""

import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level
            names.add(prefix + (node.module or ""))
    return names


def test_oracle_is_independent_of_planner_and_ranking():
    forbidden = ("clickless.planning", "clickless.ranking", "clickless.pipeline")
    for f in (ROOT / "src/evaluation/oracle").glob("*.py"):
        bad = [m for m in _imports(f) if m.startswith(forbidden)]
        assert not bad, f"{f.name} imports {bad}: the oracle must be implemented independently"


def test_contracts_import_only_contracts():
    for f in (ROOT / "src/clickless/contracts").glob("*.py"):
        for m in _imports(f):
            assert not m.startswith("clickless.") and not m.startswith(".."), f"{f.name} imports {m}"


def test_frozen_test_split_unchanged():
    frozen = ROOT / "benchmark/FROZEN.json"
    if not frozen.exists():
        return  # not frozen yet
    meta = json.loads(frozen.read_text())
    for rel, digest in meta["files"].items():
        assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == digest, f"{rel} changed after freeze"
