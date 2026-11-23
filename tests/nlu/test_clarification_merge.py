import pytest

from clickless.contracts import ClarificationQuestion, ParseResult
from clickless.nlu.clarification import TemplateClarifier, merge_answer
from clickless.nlu.rules_parser import RulesParser


def test_merge_answer_accepts_only_explicit_battery_percentage(store):
    parse_result = RulesParser(store).parse("Phoenix to Flagstaff in my comet", "merge-1")
    question = ClarificationQuestion(
        field="start_soc_pct",
        question="What percentage battery do you have right now?",
    )

    vague = merge_answer(parse_result, question, "about half", store)
    accepted = merge_answer(parse_result, question, "40 percent", store)

    assert vague.request.start_soc_pct is None
    assert "start_soc_pct" in vague.missing_fields
    assert accepted.request.start_soc_pct == 40
    assert "start_soc_pct" not in accepted.missing_fields


@pytest.mark.parametrize(
    "answer",
    ["about 70%", "around 70 percent", "I have 70%", "roughly 70 percent left", "I am at 70% battery"],
)
def test_merge_answer_accepts_one_percentage_with_natural_filler(store, answer):
    parse_result = RulesParser(store).parse("Phoenix to Flagstaff in my comet", "merge-natural-pct")
    question = ClarificationQuestion(
        field="start_soc_pct",
        question="What percentage battery do you have right now?",
    )

    merged = merge_answer(parse_result, question, answer, store)

    assert merged.request.start_soc_pct == 70
    assert "start_soc_pct" not in merged.missing_fields


@pytest.mark.parametrize(
    "answer",
    ["about 70% or 80%", "70% and 20 miles", "70 miles", "around 130%", "I have 70% charge left yesterday"],
)
def test_merge_answer_rejects_ambiguous_or_out_of_range_battery_answers(store, answer):
    parse_result = RulesParser(store).parse("Phoenix to Flagstaff in my comet", "merge-invalid-pct")
    question = ClarificationQuestion(
        field="start_soc_pct",
        question="What percentage battery do you have right now?",
    )

    merged = merge_answer(parse_result, question, answer, store)

    assert merged.request.start_soc_pct is None
    assert "start_soc_pct" in merged.missing_fields


def test_merge_answer_does_not_change_unasked_fields(store):
    parse_result = RulesParser(store).parse("Phoenix to Flagstaff in my comet", "merge-2")
    question = ClarificationQuestion(
        field="start_soc_pct",
        question="What percentage battery do you have right now?",
    )

    merged = merge_answer(parse_result, question, "65%", store)

    assert merged.request.origin == parse_result.request.origin
    assert merged.request.destination == parse_result.request.destination
    assert merged.request.vehicle_id == parse_result.request.vehicle_id


def test_merge_answer_accepts_only_an_offered_vehicle(tmp_path):
    from clickless.contracts import ClarificationQuestion, Place, TripRequest
    from clickless.data import DataStore
    from clickless.data.vehicles import load_curated_vehicles

    vehicles = load_curated_vehicles()
    store = DataStore([], vehicles, [], {}, tmp_path)
    offered = ["tesla-model-3-lr-awd-2024", "tesla-model-3-rwd-2024"]
    parse_result = ParseResult(
        request=TripRequest(
            request_id="merge-vehicle",
            raw_text="Phoenix to Flagstaff in my Model 3",
            origin=Place(text="Phoenix", lat=1, lon=1),
            destination=Place(text="Flagstaff", lat=2, lon=2),
            start_soc_pct=65,
        ),
        ambiguous_fields={"vehicle_id": offered},
        parser="rules",
    )
    question = ClarificationQuestion(
        field="vehicle_id",
        question="Which vehicle did you mean?",
        options=offered,
    )

    selected = merge_answer(parse_result, question, "2", store)
    rejected = merge_answer(parse_result, question, "an Ioniq 5", store)

    assert selected.request.vehicle_id == offered[1]
    assert "vehicle_id" not in selected.ambiguous_fields
    assert rejected.request.vehicle_id is None
    assert rejected.ambiguous_fields["vehicle_id"] == offered


def test_merge_answer_reasks_for_unresolved_place(store):
    from clickless.contracts import ClarificationQuestion

    parse_result = RulesParser(store).parse("Phoenix to Flagstaff in my comet", "merge-place")
    question = ClarificationQuestion(
        field="origin",
        question="Where are you starting?",
    )

    merged = merge_answer(parse_result, question, "Springfield", store)

    assert merged.request.origin == parse_result.request.origin
    assert merged.missing_fields == parse_result.missing_fields


def test_vague_battery_hint_cannot_turn_option_number_into_percentage(store):
    parser = RulesParser(store)
    parse_result = parser.parse(
        "Phoenix to Flagstaff in my comet, battery around half full",
        "merge-battery-hint",
    )
    question = TemplateClarifier().question_for(parse_result)

    merged = merge_answer(parse_result, question, "1", store)
    explicit = merge_answer(parse_result, question, "65%", store)

    assert question.field == "start_soc_pct"
    assert question.options == []
    assert merged.request.start_soc_pct is None
    assert "start_soc_pct" in merged.ambiguous_fields
    assert explicit.request.start_soc_pct == 65
    assert "start_soc_pct" not in explicit.ambiguous_fields
