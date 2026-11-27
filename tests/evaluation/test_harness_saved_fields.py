"""Per-row fields the harness saves so #184/#185 metrics can be computed later without a rerun."""

import json

import pytest

from clickless.config import repo_path
from evaluation import harness
from evaluation.harness import run
from evaluation.metrics.handoff import check_urls

SMOKE = "benchmark/requests/dev/fixture_smoke.jsonl"  # fixture world, Config A


@pytest.fixture(autouse=True)
def _fixture_world(monkeypatch):
    monkeypatch.delenv("CLICKLESS_DATA_SOURCE", raising=False)


@pytest.fixture
def pipes(monkeypatch):
    """Keep the pipeline each run builds, so a test can look at its store."""
    built = []
    real = harness.build_pipeline

    def keep(cfg):
        built.append(real(cfg))
        return built[-1]

    monkeypatch.setattr(harness, "build_pipeline", keep)
    return built


@pytest.fixture(scope="module")
def smoke_run(tmp_path_factory):
    global_audit = repo_path("results/handoff_audit.jsonl")
    before = global_audit.read_bytes() if global_audit.exists() else None
    out_dir = tmp_path_factory.mktemp("run")
    with pytest.MonkeyPatch.context() as mp:
        mp.delenv("CLICKLESS_DATA_SOURCE", raising=False)
        out = run("A", items_paths=[SMOKE], out_dir=str(out_dir), n_boot=20)
    after = global_audit.read_bytes() if global_audit.exists() else None
    payload = json.loads(out.read_text())
    return {"out_dir": out_dir, "payload": payload, "rows": {r["id"]: r for r in payload["rows"]},
            "global_audit_unchanged": before == after}


def _ok_rows(smoke_run):
    rows = [r for r in smoke_run["rows"].values() if r["status"] == "ok"]
    assert rows, "the fixture smoke run should plan at least one item"
    return rows


# --------------------------------------------------------------------------- handoff (#185)


def test_ok_rows_save_handoff_urls_and_expected_stops(smoke_run):
    for row in _ok_rows(smoke_run):
        recommended = row["result"]["alternatives"][0]["itinerary"]
        assert row["handoff_urls"] and "handoff_error" not in row
        assert [s["station_id"] for s in row["expected_stops"]] == [s["station_id"] for s in recommended["stops"]]
        assert all(isinstance(s["lat"], float) and isinstance(s["lon"], float) for s in row["expected_stops"])
        # the saved fields are enough to score the handoff offline
        checks = check_urls(row["handoff_urls"], [(s["lat"], s["lon"]) for s in row["expected_stops"]])
        assert all(checks.values()), checks


def test_saved_result_is_never_approved(smoke_run):
    for row in smoke_run["rows"].values():
        if "result" in row:
            assert row["result"]["approved"] is False and row["result"]["handoff_urls"] == []


def test_approval_audit_goes_to_the_run_folder(smoke_run):
    audit = smoke_run["out_dir"] / "A_handoff_audit.jsonl"
    entries = [json.loads(line) for line in audit.read_text().splitlines()]
    assert sorted(e["request_id"] for e in entries) == sorted(r["id"] for r in _ok_rows(smoke_run))
    assert smoke_run["payload"]["handoff_audit"] == str(audit)
    assert smoke_run["global_audit_unchanged"]


def test_rows_that_are_not_ok_get_no_handoff(smoke_run):
    for row in smoke_run["rows"].values():
        if row["status"] != "ok":
            assert "handoff_urls" not in row and "expected_stops" not in row


def test_a_failing_handoff_is_recorded_not_fatal(tmp_path, pipes, monkeypatch):
    real_instrument = harness.instrument

    def instrument(pipe, out_dir, name):
        hooks = real_instrument(pipe, out_dir, name)
        pipe.approve = lambda result: (_ for _ in ()).throw(KeyError("route"))
        return hooks

    monkeypatch.setattr(harness, "instrument", instrument)
    out = run("A", items_paths=[SMOKE], out_dir=str(tmp_path), n_boot=20)
    ok = [r for r in json.loads(out.read_text())["rows"] if r["status"] == "ok"]
    assert ok and all(r["handoff_error"] == "KeyError: 'route'" and r["handoff_urls"] == [] for r in ok)


# --------------------------------------------------------------------------- latency (#185)


def test_every_result_has_an_explain_lap(smoke_run):
    for row in smoke_run["rows"].values():
        timings = row["result"]["timings_ms"]
        assert list(timings)[-1] == "explain" and timings["explain"] >= 0


def test_ok_timings_cover_every_stage(smoke_run):
    for row in _ok_rows(smoke_run):
        assert list(row["result"]["timings_ms"]) == ["parse", "route", "retrieve", "plan+verify", "rank", "explain"]
        assert row["latency_ms"] >= sum(row["result"]["timings_ms"].values()) - 1  # rounding


def test_parse_error_row_keeps_latency(tmp_path, monkeypatch):
    class Parser:
        def parse(self, text, rid):
            raise ValueError("model returned no JSON")

    class Pipe:
        store = None

        def __init__(self):
            self.parser = Parser()

        def run(self, text, request_id):
            return self.parser.parse(text, request_id)

    monkeypatch.setattr(harness, "build_pipeline", lambda cfg: Pipe())
    out = run("A", items_paths=[SMOKE], out_dir=str(tmp_path), n_boot=20)
    row = next(r for r in json.loads(out.read_text())["rows"] if r["id"] == "smoke-001")
    assert row["error_stage"] == "parse" and row["latency_ms"] >= 0 and row["timings_ms"] == {}


def test_planning_error_row_keeps_latency_and_timings_so_far(tmp_path, pipes, monkeypatch):
    real_instrument = harness.instrument

    def instrument(pipe, out_dir, name):
        hooks = real_instrument(pipe, out_dir, name)

        def crash(*args, **kwargs):
            raise RuntimeError("retriever down")

        pipe.retriever.candidates = crash
        return hooks

    monkeypatch.setattr(harness, "instrument", instrument)
    out = run("A", items_paths=[SMOKE], out_dir=str(tmp_path), n_boot=20)
    row = next(r for r in json.loads(out.read_text())["rows"] if r["id"] == "smoke-001")
    assert (row["status"], row["error_stage"]) == ("error", "planning")
    assert row["latency_ms"] >= 0
    assert list(row["timings_ms"]) == ["parse", "route"]  # the stages that finished before the crash
    assert row["result"]["timings_ms"] == row["timings_ms"]


# --------------------------------------------------------------------------- ranked top 3 (#184)


def test_ok_rows_save_the_rankers_ordered_top3(smoke_run):
    for row in _ok_rows(smoke_run):
        top = row["ranked_top3"]
        assert 1 <= len(top) <= 3
        assert set(top[0]) == {"itinerary_id", "station_ids", "total_min", "amenities"}
        assert top[0]["itinerary_id"] == row["result"]["alternatives"][0]["itinerary"]["itinerary_id"]
        assert [t["total_min"] for t in top] == sorted(t["total_min"] for t in top)
        for t in top:
            assert isinstance(t["station_ids"], list)  # [] for a direct, no-stop plan
            assert all(set(a) == {"name", "category", "wheelchair"} for a in t["amenities"])


def test_top3_amenities_come_from_the_store(tmp_path, pipes):
    out = run("A", items_paths=[SMOKE], out_dir=str(tmp_path), n_boot=20)
    by_id = {a.amenity_id: a for a in pipes[0].store.amenities}
    for row in json.loads(out.read_text())["rows"]:
        if row["status"] != "ok":
            continue
        rec = row["result"]["alternatives"][0]["itinerary"]
        ids = list(dict.fromkeys(a for s in rec["stops"] for a in s["amenity_ids"]))
        expected = [{"name": by_id[i].name, "category": by_id[i].category.value, "wheelchair": by_id[i].wheelchair}
                    for i in ids]
        assert row["ranked_top3"][0]["amenities"] == expected


def test_rows_without_a_plan_have_an_empty_top3(smoke_run):
    for row in smoke_run["rows"].values():
        if row["status"] in {"rejected_incomplete", "needs_clarification"}:
            assert row["ranked_top3"] == []


def test_select_recorder_unknown_amenity_and_delegation():
    class Sel:
        presentation_config = "kept"

        def select(self, ranked, request):
            return ["picked"]

    class Stop:
        station_id, amenity_ids = "s1", ["osm:missing"]

    class It:
        itinerary_id, stops = "it1", [Stop()]

    class Cost:
        total_min = 12.5

    rec = harness.SelectRecorder(Sel(), store=None)
    assert rec.presentation_config == "kept"
    assert rec.select([(It(), Cost())], None) == ["picked"]
    assert rec.last == [{"itinerary_id": "it1", "station_ids": ["s1"], "total_min": 12.5,
                         "amenities": [{"name": None, "category": None, "wheelchair": None}]}]
