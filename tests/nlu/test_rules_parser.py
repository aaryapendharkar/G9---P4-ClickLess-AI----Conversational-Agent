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


# ---- spec for Deepti: harder phrasings the placeholder does not handle yet ----
@pytest.mark.xfail(reason="TODO(Deepti): origin/destination after a leading clause")
def test_leading_clause(parser):
    r = parser.parse("I'm in a hurry: Phoenix to Flagstaff in my Spark LR, 65% battery", "t5").request
    assert r.origin.text == "Phoenix"


@pytest.mark.xfail(reason="TODO(Deepti): 'percent' spelled out")
def test_percent_word(parser):
    assert parser.parse("from Phoenix to Flagstaff in my comet, 70 percent charge", "t6").request.start_soc_pct == 70


@pytest.mark.xfail(reason="TODO(Deepti): 'half a tank' style answers must be clarified, not guessed")
def test_vague_battery_is_ambiguous(parser):
    pr: ParseResult = parser.parse("from Phoenix to Flagstaff in my comet, about half full", "t7")
    assert "start_soc_pct" in pr.ambiguous_fields
