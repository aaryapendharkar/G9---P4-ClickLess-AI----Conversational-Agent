import json

import pytest

from clickless.nlu import llm_parser
from clickless.nlu.llm_parser import LLMParser

VALID_EXTRACTION = {
    "origin": "Phoenix",
    "destination": "Flagstaff",
    "vehicle": "Spark LR",
    "start_soc_pct": 65,
    "arrival_soc_pct_min": 20,
    "max_detour_min": 10,
    "min_power_kw": 50,
    "connectors": ["CCS"],
    "amenities": [
        {"category": "food", "phrase": "lunch", "important": True},
        {"category": "restroom", "phrase": "accessible restroom", "wheelchair_required": True, "important": True},
    ],
}


@pytest.fixture
def parser(store):
    return LLMParser(store, {"model": "test-model", "temperature": 0})


def test_valid_llm_output_is_grounded_by_datastore(parser, monkeypatch):
    monkeypatch.setattr(llm_parser, "chat", lambda *args, **kwargs: json.dumps(VALID_EXTRACTION))

    result = parser.parse("plan my Phoenix to Flagstaff trip", "llm-valid")

    assert not result.needs_input
    assert result.parser == "llm"
    assert result.request.vehicle_id == "fixture-spark-lr-2025"
    assert result.request.origin.resolved and result.request.destination.resolved
    assert result.request.connectors_override == ["CCS1"]
    assert {preference.key for preference in result.request.amenities} == {"food", "restroom_wheelchair"}
    assert all(preference.priority == "important" for preference in result.request.amenities)


def test_invalid_first_response_is_repaired_once(parser, monkeypatch):
    responses = iter(["{\"unexpected\": true}", json.dumps(VALID_EXTRACTION)])
    sent_messages = []

    def fake_chat(messages, **kwargs):
        sent_messages.append(messages)
        return next(responses)

    monkeypatch.setattr(llm_parser, "chat", fake_chat)

    result = parser.parse("plan my Phoenix to Flagstaff trip", "llm-retry")

    assert result.request.start_soc_pct == 65
    assert len(sent_messages) == 2
    assert "failed validation" in sent_messages[1][1]["content"]


def test_invalid_llm_output_falls_back_without_inventing_values(store, monkeypatch):
    monkeypatch.setattr(llm_parser, "chat", lambda *args, **kwargs: "not json")
    configured_parser = LLMParser(store, {"model": "test-model", "temperature": 0})

    result = configured_parser.parse("Phoenix to Flagstaff in my spark", "llm-fallback")

    assert result.parser == "llm"
    assert result.request.origin.resolved and result.request.destination.resolved
    assert result.request.vehicle_id == "fixture-spark-lr-2025"
    assert result.request.start_soc_pct is None
    assert result.missing_fields == ["start_soc_pct"]


def test_unknown_vehicle_is_not_assigned(parser, monkeypatch):
    response = {**VALID_EXTRACTION, "vehicle": "unknown vehicle"}
    monkeypatch.setattr(llm_parser, "chat", lambda *args, **kwargs: json.dumps(response))

    result = parser.parse("plan my trip", "llm-unknown-vehicle")

    assert result.request.vehicle_id is None
    assert "vehicle_id" in result.missing_fields


def test_versioned_prompt_is_available_as_package_data():
    prompt_path = llm_parser._PROMPT_PATH

    assert prompt_path.name == "extract_v1.txt"
    assert prompt_path.is_file()


def test_malformed_amenity_phrase_triggers_safe_fallback(store, monkeypatch):
    malformed_response = {**VALID_EXTRACTION, "amenities": [{"category": "food", "phrase": ["lunch"]}]}
    monkeypatch.setattr(llm_parser, "chat", lambda *args, **kwargs: json.dumps(malformed_response))

    result = LLMParser(store, {"model": "test-model", "temperature": 0}).parse(
        "Phoenix to Flagstaff in my spark", "llm-malformed-amenity"
    )

    assert result.parser == "llm"
    assert result.request.start_soc_pct is None
    assert result.missing_fields == ["start_soc_pct"]


def test_connector_aliases_are_normalized(parser, monkeypatch):
    response = {**VALID_EXTRACTION, "connectors": ["J1772 Combo"]}
    monkeypatch.setattr(llm_parser, "chat", lambda *args, **kwargs: json.dumps(response))

    result = parser.parse("plan my trip", "llm-connector-alias")

    assert result.request.connectors_override == ["CCS1"]
