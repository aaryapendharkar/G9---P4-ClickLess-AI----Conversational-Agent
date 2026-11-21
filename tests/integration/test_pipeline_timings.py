"""PlanResult.timings_ms reports every pipeline stage that ran, not only the parse."""

from __future__ import annotations

PLANNING_STAGES = ["parse", "route", "retrieve", "plan+verify", "rank"]


def test_planned_result_times_every_stage(pipe_a, demo_text):
    result = pipe_a.run(demo_text, "timings-ok")
    assert result.status == "ok"
    assert list(result.timings_ms) == PLANNING_STAGES  # in the order the stages ran
    assert all(isinstance(ms, float) and ms >= 0 for ms in result.timings_ms.values())


def test_stage_timings_survive_serialization(pipe_a, demo_text):
    """The harness saves results as JSON; the stages must be in what it writes."""
    saved = pipe_a.run(demo_text, "timings-json").model_dump(mode="json")
    assert list(saved["timings_ms"]) == PLANNING_STAGES


def test_request_that_stops_after_parsing_times_only_the_parse(pipe_a):
    result = pipe_a.run("plan me a trip", "timings-incomplete")
    assert result.status in {"rejected_incomplete", "needs_clarification"}
    assert list(result.timings_ms) == ["parse"]


def test_each_run_gets_its_own_timings(pipe_a, demo_text):
    first = pipe_a.run(demo_text, "timings-first")
    second = pipe_a.run(demo_text, "timings-second")
    assert first.timings_ms is not second.timings_ms
    assert list(first.timings_ms) == list(second.timings_ms) == PLANNING_STAGES
