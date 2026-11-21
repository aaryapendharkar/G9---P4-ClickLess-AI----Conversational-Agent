import json
import subprocess
import sys

import pytest

from clickless.config import repo_path
from evaluation.harness import run
from evaluation.schema import load_items

SMOKE = "benchmark/requests/dev/fixture_smoke.jsonl"  # synthetic-world items, not part of any split


def test_every_benchmark_file_validates():
    for split in ("raw", "dev", "test"):
        load_items(repo_path(f"benchmark/requests/{split}"))  # raises with file:line on error


def test_split_dirs_load_exactly_the_frozen_items(tmp_path):
    frozen = repo_path("benchmark/FROZEN.json")
    if not frozen.exists():
        pytest.skip("benchmark not frozen yet")
    counts = json.loads(frozen.read_text(encoding="utf-8"))["counts"]
    for split in ("dev", "test"):
        items = load_items(repo_path(f"benchmark/requests/{split}"))
        assert len(items) == counts[split]["n"] and not any(i.id.startswith("smoke-") for i in items)
    assert counts["dev"]["n"] == 50 and counts["test"]["n"] == 100


def test_bench_check_reports_the_frozen_counts():
    if not repo_path("benchmark/FROZEN.json").exists():
        pytest.skip("benchmark not frozen yet")
    makefile = repo_path("Makefile").read_text(encoding="utf-8").splitlines()
    recipe = makefile[makefile.index(next(ln for ln in makefile if ln.startswith("bench-check:"))) + 1].strip()
    assert recipe.startswith('python -c "') and recipe.endswith('"')
    out = subprocess.run([sys.executable, "-c", recipe[len('python -c "'):-1]], cwd=repo_path("."),
                         capture_output=True, text=True, check=True).stdout.split()  # fmt: skip
    assert out == ["raw", "150", "dev", "50", "test", "100"]


def test_load_items_skips_fixture_files_in_directories(tmp_path):
    line = repo_path(SMOKE).read_text(encoding="utf-8").splitlines()[0]
    (tmp_path / "dev.jsonl").write_text(line.replace("smoke-001", "real-001") + "\n", encoding="utf-8")
    (tmp_path / "fixture_smoke.jsonl").write_text(line + "\n", encoding="utf-8")
    assert [i.id for i in load_items(tmp_path)] == ["real-001"]
    assert [i.id for i in load_items(tmp_path / "fixture_smoke.jsonl")] == ["smoke-001"]  # explicit path loads


def test_harness_smoke(tmp_path):
    out = run("A", items_paths=[SMOKE], out_dir=str(tmp_path))
    payload = json.loads(out.read_text())
    statuses = {r["id"]: r["status"] for r in payload["rows"]}
    assert statuses["smoke-001"] == "ok" and statuses["smoke-002"] == "rejected_incomplete"
    assert payload["metrics"]["silent_assumption"]["silent_assumption_rate"] == 0.0
    metrics = payload["metrics"]
    for key in ("extraction", "extraction_city_level", "clarification", "missing_fields", "bootstrap_ci95"):
        assert "not_implemented" not in metrics[key]
    assert 0.0 <= metrics["extraction"]["micro_f1"] <= 1.0
    assert payload["run_metadata"]["config"] == "A" and payload["run_metadata"]["model_digest"] == "n/a"
    # Config A: no KG evidence, no provenance labels; the grounding metrics run and report counts
    assert metrics["grounding"]["n_results_without_evidence"] == metrics["grounding"]["n_results"]
    assert metrics["freshness"]["n_labels"] == 0


def test_harness_explicit_items(tmp_path):
    out = run("A", items_paths=["benchmark/requests/dev/fixture_smoke.jsonl"], out_dir=str(tmp_path), n_boot=50)
    payload = json.loads(out.read_text())
    assert payload["split"] is None and payload["n"] == 3
    assert payload["items"] == ["benchmark/requests/dev/fixture_smoke.jsonl"]
