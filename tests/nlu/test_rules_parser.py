import pytest

from clickless.contracts import Connector, ParseResult
from clickless.data import DataStore
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


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("use only CHAdeMO chargers", Connector.CHADEMO),
        ("use only CCS combo 1", Connector.CCS1),
        ("use only Tesla chargers", Connector.NACS),
    ],
)
def test_explicit_connector_restriction(parser, phrase, expected):
    request = parser.parse(f"Phoenix to Flagstaff in my Spark, 65%, {phrase}", "connector-only").request

    assert request.connectors_override == [expected]


def test_connector_mention_without_only_is_not_a_restriction(parser):
    request = parser.parse(
        "Phoenix to Flagstaff in my Spark, 65%, I prefer CHAdeMO chargers",
        "connector-preference",
    ).request

    assert request.connectors_override is None


@pytest.mark.parametrize(
    "route",
    [
        "From Tempe, Arizona to Flagstaff, Arizona",
        "From Tempe, AZ to Flagstaff, AZ, in my Comet",
    ],
)
def test_parses_city_state_endpoints(tmp_path, route):
    store = DataStore(
        [], [], [], {"tempe, az": (33.4255, -111.94), "flagstaff, az": (35.1983, -111.6513)}, tmp_path
    )
    request = RulesParser(store).parse(route, "city-state").request

    assert request.origin.resolved and request.origin.text == "Tempe, Az"
    assert request.destination.resolved and request.destination.text == "Flagstaff, Az"


def test_required_wheelchair_restroom_is_important_but_negation_is_not(parser):
    required = parser.parse(
        "Flagstaff to Page in my Kia EV6, 60%, I need a wheelchair accessible restroom",
        "required-restroom",
    ).request
    negated = parser.parse(
        "Flagstaff to Page in my Kia EV6, 60%, I don't need a wheelchair accessible restroom",
        "optional-restroom",
    ).request

    assert required.amenities[0].key == "restroom_wheelchair"
    assert required.amenities[0].priority == "important"
    assert negated.amenities[0].priority == "nice_to_have"


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


def test_parses_arrival_soc_when_i_arrive(parser):
    request = parser.parse(
        "Drive from Phoenix to Las Vegas in my Model Y, starting at 70%, and want at least 20% when I arrive.",
        "arrival-1",
    ).request

    assert request.start_soc_pct == 70
    assert request.arrival_soc_pct_min == 20


def test_parses_arrival_soc_on_arrival_without_reusing_start_percentage(parser):
    request = parser.parse(
        "Route from Phoenix to Albuquerque in my BMW i4 with 80% battery and at least 20% on arrival.",
        "arrival-2",
    ).request

    assert request.start_soc_pct == 80
    assert request.arrival_soc_pct_min == 20
