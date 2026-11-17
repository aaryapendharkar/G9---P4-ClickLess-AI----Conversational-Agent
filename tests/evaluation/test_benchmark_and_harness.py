import json

from clickless.config import repo_path
from evaluation.harness import run
from evaluation.schema import load_items


def test_every_benchmark_file_validates():
    for split in ("raw", "dev", "test"):
        load_items(repo_path(f"benchmark/requests/{split}"))  # raises with file:line on error


def test_harness_smoke(tmp_path):
    out = run("A", "dev", out_dir=str(tmp_path))
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
