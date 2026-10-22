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
