from clickless.contracts import ClarificationQuestion
from clickless.nlu.clarification import merge_answer
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
