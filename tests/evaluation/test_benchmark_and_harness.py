import json
import subprocess
import sys

import pytest

from clickless.config import load_config, repo_path
from clickless.contracts import PlanResult, TripRequest
from evaluation import harness
from evaluation.harness import run
from evaluation.schema import FixtureItemsRefused, load_items

SMOKE = "benchmark/requests/dev/fixture_smoke.jsonl"  # synthetic-world items, not part of any split


def test_every_benchmark_file_validates():
    for split in ("raw", "dev", "test"):
        load_items(repo_path(f"benchmark/requests/{split}"))  # raises with file:line on error


@pytest.fixture(autouse=True)
def _fixture_world_by_default(monkeypatch):
    monkeypatch.delenv("CLICKLESS_DATA_SOURCE", raising=False)  # tests opt in to snapshot explicitly


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


# --------------------------------------------------------------------------- fixture guard


def test_explicit_fixture_items_refused_on_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("CLICKLESS_DATA_SOURCE", "snapshot")
    built = []
    monkeypatch.setattr(harness, "build_pipeline", lambda cfg: built.append(cfg))
    with pytest.raises(FixtureItemsRefused, match=r"3 fixture-world item\(s\) \(smoke-001, smoke-002, smoke-003; "
                                                  r"from fixture_smoke.jsonl\).*CLICKLESS_DATA_SOURCE=fixtures"):
        run("A", items_paths=[SMOKE], out_dir=str(tmp_path))
    assert built == [] and list(tmp_path.iterdir()) == []  # refused before building anything or writing


def test_fixture_vehicle_refused_from_any_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CLICKLESS_DATA_SOURCE", "snapshot")
    item = {"id": "x-1", "author": "ian", "category": "rural", "route_type": "rural", "text": "t",
            "gold_request": {"origin": "Phoenix, AZ", "vehicle_id": "fixture-comet-awd-2026"},
            "clarification_required": False}
    f = tmp_path / "mine.jsonl"
    f.write_text(json.dumps(item) + "\n", encoding="utf-8")
    with pytest.raises(FixtureItemsRefused, match="x-1"):
        harness.select_items(load_config("A"), "dev", [str(f)])


def test_split_on_snapshot_never_loads_fixture_files(monkeypatch):
    monkeypatch.setenv("CLICKLESS_DATA_SOURCE", "snapshot")
    items, excluded = harness.select_items(load_config("A"), "dev", None)
    assert items and not any(i.id.startswith("smoke-") for i in items)  # fixture_*.jsonl skipped (load_items)
    assert excluded == []


def test_split_on_snapshot_drops_fixture_vehicle_items(tmp_path, monkeypatch):
    monkeypatch.setenv("CLICKLESS_DATA_SOURCE", "snapshot")
    split_dir = tmp_path / "requests" / "dev"
    split_dir.mkdir(parents=True)
    item = {"id": "x-1", "author": "ian", "category": "rural", "route_type": "rural", "text": "t",
            "gold_request": {"origin": "Phoenix, AZ", "vehicle_id": "fixture-comet-awd-2026"},
            "clarification_required": False}
    (split_dir / "dev.jsonl").write_text(json.dumps(item) + "\n", encoding="utf-8")
    (split_dir / "fixture_smoke.jsonl").write_text(repo_path(SMOKE).read_text(encoding="utf-8"), encoding="utf-8")
    real = harness.repo_path
    monkeypatch.setattr(harness, "repo_path",
                        lambda p: tmp_path / p.removeprefix("benchmark/") if p.startswith("benchmark/") else real(p))
    items, excluded = harness.select_items(load_config("A"), "dev", None)
    assert items == [] and excluded == ["x-1"]


def test_fixture_items_allowed_on_fixture_data(monkeypatch):
    monkeypatch.setenv("CLICKLESS_DATA_SOURCE", "fixtures")
    items, excluded = harness.select_items(load_config("A"), "dev", [SMOKE])
    assert [i.id for i in items] == ["smoke-001", "smoke-002", "smoke-003"] and excluded == []


# --------------------------------------------------------------------------- error stages


class _Parsed:
    def __init__(self, request):
        self.request = request


class _Parser:
    store = "the wrapped parser's store"

    def parse(self, text, rid):
        if rid == "smoke-001":
            raise ValueError("model returned no JSON")  # fails inside the parser
        return _Parsed(TripRequest(request_id=rid, raw_text=text, vehicle_id="fixture-spark-lr-2025"))


class _Pipe:
    """Parses first, like the real pipeline, then 'plans'."""

    store = None  # the freshness metric reads pipe.store; it reports an error for None

    def __init__(self):
        self.parser = _Parser()

    def run(self, text, request_id):
        parsed = self.parser.parse(text, request_id)
        if request_id == "smoke-002":
            raise KeyError("vehicle")  # planning raises after a successful parse
        return PlanResult(request_id=request_id, config="A", status="error", request=parsed.request,
                          notes=["no cached route at routes/phoenix__flagstaff.json"])


def test_harness_records_error_stage_and_keeps_going(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "build_pipeline", lambda cfg: _Pipe())
    out = run("A", items_paths=[SMOKE], out_dir=str(tmp_path), n_boot=20)
    rows = {r["id"]: r for r in json.loads(out.read_text())["rows"]}
    assert (rows["smoke-001"]["status"], rows["smoke-001"]["error_stage"], rows["smoke-001"]["error_type"]) == (
        "error", "parse", "ValueError")
    assert rows["smoke-001"]["error"] == "ValueError: model returned no JSON"
    assert "result" not in rows["smoke-001"]  # nothing was parsed, so nothing to score
    assert (rows["smoke-002"]["error_stage"], rows["smoke-002"]["error_type"]) == ("planning", "KeyError")
    assert rows["smoke-003"]["status"] == "error" and rows["smoke-003"]["result"]["request"] is not None


def test_planning_exception_keeps_and_scores_the_parse(tmp_path, monkeypatch):
    from evaluation.report import headline, load_runs

    monkeypatch.setattr(harness, "build_pipeline", lambda cfg: _Pipe())
    out = run("A", items_paths=[SMOKE], out_dir=str(tmp_path), n_boot=20)
    payload = json.loads(out.read_text())
    row = next(r for r in payload["rows"] if r["id"] == "smoke-002")
    # counted as a planning error ...
    assert (row["status"], row["error_stage"], row["error_type"]) == ("error", "planning", "KeyError")
    assert row["result"]["status"] == "error" and row["result"]["notes"] == ["KeyError: 'vehicle'"]
    # ... and its parse is kept and scored
    assert row["result"]["request"]["vehicle_id"] == "fixture-spark-lr-2025" and row["latency_ms"] >= 0
    extraction = payload["metrics"]["extraction"]
    assert extraction["n"] == 2  # smoke-002 (planning raised) and smoke-003; not smoke-001 (parse raised)
    # smoke-002's parsed vehicle matches its gold (the TP comes from the item whose planning raised);
    # smoke-003's gold is the Comet, so the fake's Spark is one FP + one FN
    assert (extraction["vehicle_id.tp"], extraction["vehicle_id.fp"], extraction["vehicle_id.fn"]) == (1, 1, 1)

    runs, _ = load_runs(out.parent)
    h = headline(runs["A"], n_boot=20)
    assert h["parse_error_rate"][0] == pytest.approx(1 / 3)
    assert h["planning_error_rate"][0] == pytest.approx(2 / 3)  # smoke-002 (exception) + smoke-003 (status)
    assert runs["A"].error["smoke-002"] == ("planning", "KeyError")


def test_parse_recorder_delegates_and_resets():
    rec = harness.ParseRecorder(_Parser())
    assert rec.store == "the wrapped parser's store"
    assert rec.parse("t", "x").request.request_id == "x" and rec.last.request.request_id == "x"
    with pytest.raises(ValueError):
        rec.parse("t", "smoke-001")
    assert rec.last is None  # a failed parse never leaves the previous item's request behind


def test_exception_stage():
    parser = _Parser()
    try:
        parser.parse("t", "r")
    except ValueError as e:
        assert harness.exception_stage(e, parser) == "parse"
        assert harness.exception_stage(e, object()) == "planning"
