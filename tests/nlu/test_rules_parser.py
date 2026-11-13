import pytest

from clickless.contracts import ParseResult
from clickless.nlu.clarification import TemplateClarifier
from clickless.nlu.rules_parser import RulesParser


@pytest.fixture
def parser(store):
    return RulesParser(store)


def test_parses_demo_request(parser, demo_text):
    r = parser.parse(demo_text, "t1").request
    assert (r.origin.text, r.destination.text) == ("Phoenix", "Flagstaff")
    assert r.vehicle_id == "fixture-spark-lr-2025"
    assert (r.start_soc_pct, r.arrival_soc_pct_min) == (65, 20)
    assert {p.key for p in r.amenities} == {"food", "restroom_wheelchair"}


def test_missing_battery_is_reported_not_invented(parser):
    pr = parser.parse("Phoenix to Flagstaff in my spark", "t2")
    assert pr.missing_fields == ["start_soc_pct"] and pr.request.start_soc_pct is None


def test_priority_phrase_sets_profile(parser):
    r = parser.parse("from Phoenix to Flagstaff, comet, 80%, I'm in a hurry", "t3").request
    assert r.tradeoff_profile == "fastest"


def test_clarifier_asks_for_battery(parser):
    pr = parser.parse("Phoenix to Flagstaff in my spark", "t4")
    assert TemplateClarifier().question_for(pr).field == "start_soc_pct"


def test_leading_clause(parser):
    r = parser.parse("I'm in a hurry: Phoenix to Flagstaff in my Spark LR, 65% battery", "t5").request
    assert r.origin.text == "Phoenix"


def test_percent_word(parser):
    assert parser.parse("from Phoenix to Flagstaff in my comet, 70 percent charge", "t6").request.start_soc_pct == 70


def test_vague_battery_is_ambiguous(parser):
    pr: ParseResult = parser.parse("from Phoenix to Flagstaff in my comet, about half full", "t7")
    assert "start_soc_pct" in pr.ambiguous_fields


def test_clarifier_prioritizes_ambiguous_safety_field(parser):
    parse_result = parser.parse("Phoenix to Flagstaff with 70%", "t8").model_copy(
        update={"ambiguous_fields": {"vehicle_id": ["vehicle-a", "vehicle-b"]}}
    )

    question = TemplateClarifier().question_for(parse_result)

    assert question.field == "vehicle_id"
    assert question.options == ["vehicle-a", "vehicle-b"]
    assert "Which vehicle did you mean?" == question.question


def test_clarifier_explains_ambiguous_origin(parser):
    parse_result = parser.parse("Phoenix to Chicago in my Spark LR with 70%", "t9").model_copy(
        update={"ambiguous_fields": {"origin": ["Springfield, IL", "Springfield, MO"]}}
    )

    question = TemplateClarifier().question_for(parse_result)

    assert question.field == "origin"
    assert question.options == ["Springfield, IL", "Springfield, MO"]
    assert "starting location" in question.question


def test_clarifier_orders_multiple_missing_safety_fields(parser):
    parse_result = parser.parse("Phoenix to Flagstaff", "t10")

    question = TemplateClarifier().question_for(parse_result)

    assert parse_result.missing_fields == ["vehicle_id", "start_soc_pct"]
    assert question.field == "vehicle_id"
