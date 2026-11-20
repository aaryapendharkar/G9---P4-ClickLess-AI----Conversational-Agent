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


def test_harness_records_a_crashing_item_and_keeps_going(tmp_path, monkeypatch):
    import evaluation.harness as harness

    real = harness.build_pipeline

    def crashing_pipeline(cfg):
        pipe = real(cfg)
        run_one = pipe.run

        def run_item(text, request_id):
            if request_id == "smoke-001":
                raise ValueError("'route destination' appears before 'X' on the route")
            return run_one(text, request_id=request_id)

        pipe.run = run_item
        return pipe

    monkeypatch.setattr(harness, "build_pipeline", crashing_pipeline)
    out = run("A", items_paths=["benchmark/requests/dev/fixture_smoke.jsonl"], out_dir=str(tmp_path), n_boot=50)
    rows = {r["id"]: r for r in json.loads(out.read_text())["rows"]}
    assert len(rows) == 3  # the run went on after the crash
    assert rows["smoke-001"]["status"] == "error" and rows["smoke-001"]["error_type"] == "ValueError"
    assert rows["smoke-001"]["error"] == "ValueError: 'route destination' appears before 'X' on the route"
    assert rows["smoke-002"]["status"] == "rejected_incomplete"
