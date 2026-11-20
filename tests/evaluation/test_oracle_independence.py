"""The oracle (and the metrics that judge the planner) must not use planner, ranking or retrieval code.

Two checks: a static one over the source (every import form, including `from clickless import
planning` and relative imports), and a runtime one in a fresh interpreter (transitive imports
through data/, contracts/ or config).
"""

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
FORBIDDEN = ("clickless.planning", "clickless.ranking", "clickless.retrieval", "clickless.pipeline")
GUARDED = [
    *sorted((SRC / "evaluation/oracle").glob("*.py")),
    SRC / "evaluation/metrics/planning.py",
    SRC / "evaluation/metrics/tradeoffs.py",
]


def imported_names(source: str, package: str) -> set[str]:
    """Every absolute module name an import statement can refer to (module and module.alias)."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = package.split(".")
                parts = parts[: len(parts) - (node.level - 1)]
                module = ".".join(parts + ([node.module] if node.module else []))
            else:
                module = node.module or ""
            names.add(module)
            names |= {f"{module}.{a.name}" for a in node.names}
    return names


def forbidden_in(names: set[str]) -> list[str]:
    return sorted(n for n in names if any(n == f or n.startswith(f + ".") for f in FORBIDDEN))


def _package(path: Path) -> str:
    return ".".join(path.relative_to(SRC).with_suffix("").parts[:-1])


@pytest.mark.parametrize("path", GUARDED, ids=lambda p: p.name)
def test_no_forbidden_import_statements(path):
    bad = forbidden_in(imported_names(path.read_text(encoding="utf-8"), _package(path)))
    assert not bad, f"{path.relative_to(ROOT)} imports {bad}: the oracle must stay independent of the planner"


@pytest.mark.parametrize(
    "line",
    [
        "import clickless.planning.energy",
        "from clickless import planning",
        "from clickless.retrieval.corridor import CorridorRetriever",
        "from clickless import ranking as r",
        "from clickless import pipeline",
        "from ...planning import filters",  # relative: inside package clickless.x.y this is clickless.planning
    ],
)
def test_static_check_catches_every_import_form(line):
    assert forbidden_in(imported_names(line, "clickless.x.y"))


def test_relative_imports_are_resolved():
    names = imported_names((SRC / "evaluation/metrics/planning.py").read_text(encoding="utf-8"), "evaluation.metrics")
    assert "evaluation.oracle.label_setting" in names


def test_no_forbidden_module_loaded_at_runtime():
    code = (
        f"import sys; sys.path.insert(0, {str(SRC)!r})\n"
        "import evaluation.oracle.label_setting, evaluation.oracle.energy_reference, evaluation.metrics.planning\n"
        "import evaluation.metrics.tradeoffs\n"
        f"print('\\n'.join(sorted(m for m in sys.modules if m.startswith({FORBIDDEN!r}))))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "", f"importing the oracle loads planner-side modules:\n{out.stdout}"
