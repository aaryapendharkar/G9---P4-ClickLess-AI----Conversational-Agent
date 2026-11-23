"""#140: a trip whose route is not in the offline route cache gets a friendly message, not
"Something went wrong" plus a local file path. The path stays in PlanResult.notes for debugging
and for the eval report's RouteNotCached grouping."""

import pytest

import clickless.cli as cli
from clickless.common.errors import NO_CACHED_ROUTE_NOTE
from clickless.config import load_config
from clickless.contracts import PlanResult, TripRequest
from clickless.pipeline import build_pipeline

FRIENDLY = "I don't have route data for Flagstaff -> Phoenix yet, so I can't plan this trip offline."


def _uncached_request(store):
    # the fixture route cache holds phoenix__flagstaff only, so the reverse trip is not cached
    return TripRequest(
        request_id="uncached",
        raw_text="Flagstaff to Phoenix",
        origin=store.geocode("Flagstaff"),
        destination=store.geocode("Phoenix"),
        vehicle_id=next(iter(store.vehicles)),
        start_soc_pct=80,
        arrival_soc_pct_min=20,
    )


@pytest.mark.parametrize("config", ["B", "C"])
def test_an_uncached_route_gets_a_friendly_message(store, config):
    pipe = build_pipeline(load_config(config), store)
    req = _uncached_request(store)
    result = pipe.plan_request(req, PlanResult(request_id=req.request_id, config=config, status="ok", request=req))

    assert result.status == "error" and not result.alternatives
    assert result.explanation == FRIENDLY
    assert "Something went wrong" not in result.explanation
    assert str(store.routes_dir) not in result.explanation and ".json" not in result.explanation
    assert result.claims == []
    # the debugging detail is kept in the result (and so in --json and saved eval results)
    assert len(result.notes) == 1 and result.notes[0].startswith(f"{NO_CACHED_ROUTE_NOTE} at ")
    assert result.notes[0].endswith("flagstaff__phoenix.json")


def test_any_other_error_keeps_the_generic_message(pipe_a):
    result = PlanResult(request_id="e", config="A", status="error", notes=["planner crashed"])
    text, _ = pipe_a.explainer.explain(result)
    assert text == "Something went wrong while planning."


class _OnePipeline:
    def __init__(self, result):
        self.result = result

    def run(self, text):
        return self.result


@pytest.mark.parametrize("config", ["B", "C"])
def test_the_cli_shows_the_message_but_not_the_cache_path(store, monkeypatch, capsys, config):
    pipe = build_pipeline(load_config(config), store)
    req = _uncached_request(store)
    result = pipe.plan_request(req, PlanResult(request_id=req.request_id, config=config, status="ok", request=req))
    result.notes.append("parser: some other diagnostic")
    monkeypatch.setattr(cli, "build_pipeline", lambda _cfg: _OnePipeline(result))

    assert cli.main(["plan", "--config", config, "--no-input", "Flagstaff to Phoenix"]) == 0
    out = capsys.readouterr().out
    assert FRIENDLY in out
    assert NO_CACHED_ROUTE_NOTE not in out and str(store.routes_dir) not in out
    assert "[notes] parser: some other diagnostic" in out  # other notes are still printed

    assert cli.main(["plan", "--config", config, "--no-input", "--json", "Flagstaff to Phoenix"]) == 0
    assert "flagstaff__phoenix.json" in capsys.readouterr().out  # --json keeps the detail
