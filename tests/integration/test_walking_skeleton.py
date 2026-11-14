"""End-to-end Config A on fixtures. If this breaks on main, the integration captain fixes it first."""

from clickless.config import load_config
from clickless.pipeline import build_pipeline


def test_config_a_plans_one_stop_trip(pipe_a, demo_text):
    r = pipe_a.run(demo_text, request_id="it-1")
    assert r.status == "ok"
    rec = r.alternatives[0].itinerary
    assert len(rec.stops) == 1
    assert r.verification.ok
    assert rec.arrival_soc_pct >= 20


def test_no_handoff_without_approval(pipe_a, demo_text):
    r = pipe_a.run(demo_text, request_id="it-2")
    assert r.handoff_urls == [] and not r.approved


def test_handoff_after_approval(pipe_a, demo_text, tmp_path):
    pipe_a.gate.audit_log = tmp_path / "audit.jsonl"
    r = pipe_a.approve(pipe_a.run(demo_text, request_id="it-3"))
    assert r.approved and len(r.handoff_urls) == 1
    assert r.handoff_urls[0].startswith("https://www.google.com/maps/dir/?api=1")
    assert "waypoints=" in r.handoff_urls[0]


def test_tradeoff_alternative_is_offered(pipe_a, demo_text):
    r = pipe_a.run(demo_text, request_id="it-4")
    labels = [a.label for a in r.alternatives]
    assert labels[0] == "Recommended" and "Fastest" in labels
    fastest = next(a for a in r.alternatives if a.label == "Fastest")
    assert fastest.deltas["total_time_min"] < 0 and fastest.losses


def test_config_a_rejects_incomplete_request(pipe_a):
    r = pipe_a.run("Phoenix to Flagstaff in my spark", request_id="it-5")
    assert r.status == "rejected_incomplete" and "start_soc_pct" in r.notes


def test_direct_trip_needs_no_stop(pipe_a):
    r = pipe_a.run("Phoenix to Flagstaff in my comet with 90%", request_id="it-6")
    assert r.status == "ok" and r.alternatives[0].itinerary.stops == []


def test_config_c_clarifies_incomplete_request():
    pipe_c = build_pipeline(load_config("C"))
    r = pipe_c.run("Phoenix to Flagstaff in my spark", request_id="it-7")
    assert r.status == "needs_clarification" and r.clarification.field == "start_soc_pct"


def test_config_c_clarification_continues_after_answer(pipe_a):
    from clickless.nlu.clarification import TemplateClarifier

    pipe_a.clarifier = TemplateClarifier()
    first = pipe_a.run("Phoenix to Flagstaff in my comet", request_id="multi-1")

    assert first.status == "needs_clarification"
    assert first.clarification.field == "start_soc_pct"

    completed = pipe_a.continue_clarification("multi-1", "65%")

    assert completed.status == "ok"
    assert completed.request.start_soc_pct == 65
    assert completed.request.request_id == "multi-1"


def test_config_c_rejects_two_invalid_clarification_answers(pipe_a):
    from clickless.nlu.clarification import TemplateClarifier

    pipe_a.clarifier = TemplateClarifier()
    pipe_a.run("Phoenix to Flagstaff in my comet", request_id="multi-2")

    first_retry = pipe_a.continue_clarification("multi-2", "about half")
    rejected = pipe_a.continue_clarification("multi-2", "still half")

    assert first_retry.status == "needs_clarification"
    assert rejected.status == "rejected_incomplete"
    assert "start_soc_pct" in rejected.notes
