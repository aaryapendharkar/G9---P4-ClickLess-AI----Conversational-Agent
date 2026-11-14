"""Issue #36: data-layer text files are read and written as UTF-8 on every OS.

Without `encoding=`, Python uses the locale encoding (cp1252 on most Windows machines), which
cannot write the snapshot report's "→" and can misread non-ASCII text in benchmark files. This
check runs on Linux CI too, so a new call without an explicit encoding fails before it reaches a
teammate's Windows machine.
"""

import ast

import pytest

from clickless.config import repo_path

DATA_MODULES = sorted(repo_path("src/clickless/data").glob("*.py"))


def _text_io_calls_without_encoding(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_text_method = isinstance(func, ast.Attribute) and func.attr in {"read_text", "write_text"}
        is_open = isinstance(func, ast.Name) and func.id == "open"
        if not (is_text_method or is_open):
            continue
        if any(kw.arg == "encoding" for kw in node.keywords):
            continue
        mode = node.args[1] if is_open and len(node.args) > 1 else next(
            (kw.value for kw in node.keywords if kw.arg == "mode"), None
        )
        if isinstance(mode, ast.Constant) and "b" in str(mode.value):
            continue  # binary I/O has no encoding
        yield f"{path.name}:{node.lineno}"


def test_data_modules_found():
    assert {"snapshot_report.py", "build_snapshot.py", "loaders.py"} <= {p.name for p in DATA_MODULES}


@pytest.mark.parametrize("path", DATA_MODULES, ids=lambda p: p.name)
def test_text_io_has_explicit_utf8_encoding(path):
    assert list(_text_io_calls_without_encoding(path)) == []
