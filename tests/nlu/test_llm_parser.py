import json

import pytest
import requests

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

    result = parser.parse("plan my Phoenix to Flagstaff trip using CCS", "llm-valid")

    assert not result.needs_input
    assert result.parser == "llm"
    assert result.request.vehicle_id == "fixture-spark-lr-2025"
    assert result.request.origin.resolved and result.request.destination.resolved
    assert result.request.connectors_override == ["CCS1"]
    assert {preference.key for preference in result.request.amenities} == {"food", "restroom_wheelchair"}
    assert all(preference.priority == "important" for preference in result.request.amenities)


def test_explicit_need_overrides_llm_amenity_priority_but_negation_does_not(parser, monkeypatch):
    response = {
        **VALID_EXTRACTION,
        "amenities": [
            {
                "category": "restroom",
                "phrase": "wheelchair accessible restroom",
                "wheelchair_required": True,
                "important": False,
            }
        ],
    }
    monkeypatch.setattr(llm_parser, "chat", lambda *args, **kwargs: json.dumps(response))

    required = parser.parse("I need a wheelchair accessible restroom", "llm-required-restroom")
    negated = parser.parse("I don't need a wheelchair accessible restroom", "llm-optional-restroom")

    assert required.request.amenities[0].priority == "important"
    assert negated.request.amenities[0].priority == "nice_to_have"


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

    assert result.parser == "rules_fallback"
    assert result.fallback_reason
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

    assert prompt_path.name == "extract_v2.txt"
    assert prompt_path.is_file()


def test_malformed_amenity_phrase_triggers_safe_fallback(store, monkeypatch):
    malformed_response = {**VALID_EXTRACTION, "amenities": [{"category": "food", "phrase": ["lunch"]}]}
    monkeypatch.setattr(llm_parser, "chat", lambda *args, **kwargs: json.dumps(malformed_response))

    result = LLMParser(store, {"model": "test-model", "temperature": 0}).parse(
        "Phoenix to Flagstaff in my spark", "llm-malformed-amenity"
    )

    assert result.parser == "rules_fallback"
    assert result.fallback_reason
    assert result.request.start_soc_pct is None
    assert result.missing_fields == ["start_soc_pct"]


def test_connector_aliases_are_normalized(parser, monkeypatch):
    response = {**VALID_EXTRACTION, "connectors": ["J1772 Combo"]}
    monkeypatch.setattr(llm_parser, "chat", lambda *args, **kwargs: json.dumps(response))

    result = parser.parse("Use a J1772 Combo charger on my trip", "llm-connector-alias")

    assert result.request.connectors_override == ["CCS1"]


@pytest.mark.parametrize(
    "request_text",
    [
        "Ely to Tonopah in my Nissan Leaf, 10% battery, arrive with at least 60%, only 350 kW chargers",
        (
            "I'm in a hurry from Los Angeles to San Diego in my 2024 Tesla Model 3 Long Range AWD at 35%, "
            "but I also need a sit-down meal and coffee while charging."
        ),
        (
            "My 2023 Rivian R1S with the large pack is down to 9%. Houston Galleria to Hobby Airport, "
            "and I'd like a quick bite while it charges."
        ),
    ],
)
def test_connector_is_not_inferred_without_an_explicit_plug_mention(parser, monkeypatch, request_text):
    response = {**VALID_EXTRACTION, "connectors": ["CCS"]}
    monkeypatch.setattr(llm_parser, "chat", lambda *args, **kwargs: json.dumps(response))

    result = parser.parse(request_text, "llm-no-invented-connector")

    assert result.request.connectors_override is None


def test_invalid_connector_output_falls_back_safely(store, monkeypatch):
    response = {**VALID_EXTRACTION, "connectors": ["unknown-port"]}
    monkeypatch.setattr(llm_parser, "chat", lambda *args, **kwargs: json.dumps(response))

    result = LLMParser(store, {"model": "test-model", "temperature": 0}).parse(
        "Phoenix to Flagstaff in my spark", "llm-invalid-connector"
    )

    assert result.parser == "rules_fallback"
    assert result.fallback_reason
    assert result.request.start_soc_pct is None
    assert result.missing_fields == ["start_soc_pct"]


def test_out_of_range_battery_output_falls_back_safely(store, monkeypatch):
    response = {**VALID_EXTRACTION, "start_soc_pct": 140}
    monkeypatch.setattr(llm_parser, "chat", lambda *args, **kwargs: json.dumps(response))

    result = LLMParser(store, {"model": "test-model", "temperature": 0}).parse(
        "Phoenix to Flagstaff in my spark", "llm-invalid-battery"
    )

    assert result.parser == "rules_fallback"
    assert result.fallback_reason
    assert result.request.start_soc_pct is None
    assert result.missing_fields == ["start_soc_pct"]


def test_parser_uses_environment_model_when_config_is_placeholder(parser, monkeypatch):
    captured = {}

    def fake_chat(messages, **kwargs):
        captured.update(kwargs)
        return json.dumps(VALID_EXTRACTION)

    monkeypatch.setenv("VOYAGER_MODEL", "qwen3:8b")
    monkeypatch.setattr(llm_parser, "chat", fake_chat)

    LLMParser(parser.store, {"model": "CHANGE_ME", "temperature": 0}).parse(
        "plan my Phoenix to Flagstaff trip", "llm-env-model"
    )

    assert captured["model"] == "qwen3:8b"
    assert captured["temperature"] == 0.0
    assert captured["think"] is False


def test_internal_parser_error_is_not_treated_as_unavailable(parser, monkeypatch):
    monkeypatch.setattr(llm_parser, "chat", lambda *args, **kwargs: json.dumps(VALID_EXTRACTION))
    monkeypatch.setattr(parser, "_ground", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("internal bug")))

    with pytest.raises(RuntimeError, match="internal bug"):
        parser.parse("plan my Phoenix to Flagstaff trip", "llm-internal-error")


def test_timeout_fallback_metadata_is_bounded(parser, monkeypatch):
    monkeypatch.setattr(llm_parser, "chat", lambda *args, **kwargs: (_ for _ in ()).throw(
        requests.ReadTimeout("x" * 5000)
    ))

    result = parser.parse("Phoenix to Flagstaff in my spark", "fallback-timeout")

    assert result.parser == "rules_fallback"
    assert result.fallback_reason.startswith("unavailable: ReadTimeout")
    assert len(result.fallback_reason) <= llm_parser.MAX_FALLBACK_REASON_LENGTH


def test_connection_error_fallback_metadata(parser, monkeypatch):
    monkeypatch.setattr(llm_parser, "chat", lambda *args, **kwargs: (_ for _ in ()).throw(
        requests.ConnectionError("connection refused")
    ))

    result = parser.parse("Phoenix to Flagstaff in my spark", "fallback-connection")

    assert result.parser == "rules_fallback"
    assert result.fallback_reason.startswith("unavailable: ConnectionError")


def test_non_string_model_response_is_repaired_or_falls_back(parser, monkeypatch):
    monkeypatch.setattr(llm_parser, "chat", lambda *args, **kwargs: None)

    result = parser.parse("Phoenix to Flagstaff in my spark", "fallback-none")

    assert result.parser == "rules_fallback"
    assert result.fallback_reason.startswith("invalid_output: attempt 2")
