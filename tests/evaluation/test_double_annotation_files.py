"""benchmark/labels/double/<annotator>.jsonl: blind templates for the 40 double-annotated items."""

import json

import pytest

from clickless.config import load_config, repo_path
from evaluation.schema import load_items
from evaluation.splits import BENCHMARK_CONFIG, assign_double_annotation

DOUBLE_DIR = repo_path("benchmark/labels/double")
KEYS = {"id", "text", "category", "gold_request", "clarification_required", "missing_safety_fields",
        "hard_constraints", "soft_constraints", "notes"}  # fmt: skip


def _rows(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(ln) for ln in lines if ln.strip() and not ln.lstrip().startswith("//")]


def test_double_annotation_files_match_the_assignment_and_stay_blind():
    if not repo_path("benchmark/FROZEN.json").exists():
        pytest.skip("benchmark not frozen yet")
    raw = {i.id: i for i in load_items(repo_path("benchmark/requests/raw"))}
    dev = {i.id for i in load_items(repo_path("benchmark/requests/dev"))}
    cfg = load_config(BENCHMARK_CONFIG)
    expected: dict[str, set[str]] = {}
    for item_id, annotator in assign_double_annotation(
        list(raw.values()), dev_ids=dev, dev_only_annotators=tuple(cfg["dev_only_annotators"])
    ):
        expected.setdefault(annotator, set()).add(item_id)

    files = {p.stem: _rows(p) for p in DOUBLE_DIR.glob("*.jsonl")}
    assert {a: {r["id"] for r in rows} for a, rows in files.items()} == expected
    for rows in files.values():
        for r in rows:
            assert set(r) == KEYS  # nothing copied from the original item (author, gold, notes, ...)
            assert r["text"] == raw[r["id"]].text
