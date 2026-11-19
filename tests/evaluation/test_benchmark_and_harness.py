import json

import pytest

from clickless.config import load_config, repo_path
from clickless.contracts import PlanResult, TripRequest
from evaluation import harness
from evaluation.harness import run
from evaluation.schema import FixtureItemsRefused, load_items


def test_every_benchmark_file_validates():
    for split in ("raw", "dev", "test"):
        load_items(repo_path(f"benchmark/requests/{split}"))  # raises with file:line on error


@pytest.fixture(autouse=True)
def _fixture_world_by_default(monkeypatch):
    monkeypatch.delenv("CLICKLESS_DATA_SOURCE", raising=False)  # tests opt in to snapshot explicitly


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


def test_harness_explicit_items(tmp_path):
    out = run("A", items_paths=["benchmark/requests/dev/fixture_smoke.jsonl"], out_dir=str(tmp_path), n_boot=50)
    payload = json.loads(out.read_text())
    assert payload["split"] is None and payload["n"] == 3
    assert payload["items"] == ["benchmark/requests/dev/fixture_smoke.jsonl"]


# --------------------------------------------------------------------------- fixture guard

SMOKE = "benchmark/requests/dev/fixture_smoke.jsonl"


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


def test_split_on_snapshot_skips_fixture_items(monkeypatch):
    monkeypatch.setenv("CLICKLESS_DATA_SOURCE", "snapshot")
    items, excluded = harness.select_items(load_config("A"), "dev", None)
    assert excluded == ["smoke-001", "smoke-002", "smoke-003"]
    assert not {i.id for i in items} & set(excluded)


def test_fixture_items_allowed_on_fixture_data(monkeypatch):
    monkeypatch.setenv("CLICKLESS_DATA_SOURCE", "fixtures")
    items, excluded = harness.select_items(load_config("A"), "dev", [SMOKE])
    assert [i.id for i in items] == ["smoke-001", "smoke-002", "smoke-003"] and excluded == []


# --------------------------------------------------------------------------- error stages


class _Parser:
    def parse(self, text, rid):
        raise ValueError("model returned no JSON")


class _Pipe:
    parser = _Parser()

    def run(self, text, request_id):
        if request_id == "smoke-001":
            return self.parser.parse(text, request_id)  # fails inside the parser
        if request_id == "smoke-002":
            raise KeyError("vehicle")  # fails after parsing
        return PlanResult(request_id=request_id, config="A", status="error",
                          request=TripRequest(request_id=request_id, raw_text=text),
                          notes=["no cached route at routes/phoenix__flagstaff.json"])


def test_harness_records_error_stage_and_keeps_going(tmp_path, monkeypatch):
    monkeypatch.delenv("CLICKLESS_DATA_SOURCE", raising=False)
    monkeypatch.setattr(harness, "build_pipeline", lambda cfg: _Pipe())
    out = run("A", items_paths=[SMOKE], out_dir=str(tmp_path), n_boot=20)
    rows = {r["id"]: r for r in json.loads(out.read_text())["rows"]}
    assert (rows["smoke-001"]["status"], rows["smoke-001"]["error_stage"], rows["smoke-001"]["error_type"]) == (
        "error", "parse", "ValueError")
    assert rows["smoke-001"]["error"] == "ValueError: model returned no JSON"
    assert (rows["smoke-002"]["error_stage"], rows["smoke-002"]["error_type"]) == ("planning", "KeyError")
    assert rows["smoke-003"]["status"] == "error" and rows["smoke-003"]["result"]["request"] is not None


def test_exception_stage():
    parser = _Parser()
    try:
        parser.parse("t", "r")
    except ValueError as e:
        assert harness.exception_stage(e, parser) == "parse"
        assert harness.exception_stage(e, object()) == "planning"
