"""Configurations B/C parser: grounded JSON extraction through Voyager. Owner: Deepti.

The model may suggest strings and preferences, but the datastore remains authoritative for place
coordinates and vehicle ids. Missing or ambiguous safety-critical fields are preserved as such;
this parser never fills them with a default.
"""

from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path
from typing import Any

from ..config import repo_path
from ..contracts import AmenityCategory, AmenityPreference, Connector, ParseResult, TripRequest
from .priority_phrases import detect_priority_keys, detect_profile, detect_weight_overrides
from .rules_parser import RulesParser
from .voyager_client import chat

_LOG = logging.getLogger(__name__)
_PROMPT_PATH = Path(__file__).with_name("prompts") / "extract_v1.txt"
_ALLOWED_FIELDS = {
    "origin", "destination", "vehicle", "start_soc_pct", "arrival_soc_pct_min",
    "max_detour_min", "min_power_kw", "amenities", "connectors",
}
_ALLOWED_AMENITY_FIELDS = {"category", "phrase", "wheelchair_required", "important"}
_NUMERIC_RANGES = {
    "start_soc_pct": (0.0, 100.0),
    "arrival_soc_pct_min": (0.0, 100.0),
    "max_detour_min": (0.0, math.inf),
    "min_power_kw": (0.0, math.inf),
}
_CONNECTOR_ALIASES = {
    "ccs": Connector.CCS1,
    "ccs1": Connector.CCS1,
    "combo 1": Connector.CCS1,
    "ccs combo 1": Connector.CCS1,
    "j1772 combo": Connector.CCS1,
    "nacs": Connector.NACS,
    "tesla": Connector.NACS,
    "chademo": Connector.CHADEMO,
    "j1772": Connector.J1772,
}

_EXTRACTION_SCHEMA = {
    "type": "object",
    "properties": {
        "origin": {"type": ["string", "null"]},
        "destination": {"type": ["string", "null"]},
        "vehicle": {"type": ["string", "null"]},
        "start_soc_pct": {"type": ["number", "null"]},
        "arrival_soc_pct_min": {"type": ["number", "null"]},
        "max_detour_min": {"type": ["number", "null"]},
        "min_power_kw": {"type": ["number", "null"]},
        "amenities": {"type": "array", "items": {"type": "object"}},
        "connectors": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}


def _json_object(raw_response: str) -> dict[str, Any]:
    """Accept JSON-only output plus a fenced JSON response, rejecting other text."""
    cleaned_response = raw_response.strip()
    fenced_match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned_response, re.DOTALL | re.IGNORECASE)
    if fenced_match:
        cleaned_response = fenced_match.group(1)
    parsed_response = json.loads(cleaned_response)
    if not isinstance(parsed_response, dict):
        raise ValueError("LLM response must be a JSON object")
    return parsed_response


def _normalize_connector(value: str) -> Connector:
    """Map common driver/model phrases to the canonical connector enum."""
    normalized_value = re.sub(r"\s+", " ", value.strip().lower())
    try:
        return _CONNECTOR_ALIASES[normalized_value]
    except KeyError as error:
        raise ValueError(f"unsupported connector: {value}") from error


def _validate_extraction(extracted: dict[str, Any]) -> None:
    """Reject malformed model output before it can enter the shared request contract."""
    unknown_fields = set(extracted) - _ALLOWED_FIELDS
    if unknown_fields:
        raise ValueError(f"unsupported extraction fields: {sorted(unknown_fields)}")

    for field_name, (minimum, maximum) in _NUMERIC_RANGES.items():
        value = extracted.get(field_name)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{field_name} must be a finite number or null")
        if not minimum <= value <= maximum:
            raise ValueError(f"{field_name} is outside its allowed range")

    for field_name in ("origin", "destination", "vehicle"):
        value = extracted.get(field_name)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{field_name} must be a string or null")

    connectors = extracted.get("connectors", [])
    if not isinstance(connectors, list) or any(not isinstance(value, str) for value in connectors):
        raise ValueError("connectors must be a list of strings")
    for connector in connectors:
        _normalize_connector(connector)

    amenities = extracted.get("amenities", [])
    if not isinstance(amenities, list):
        raise ValueError("amenities must be a list")
    valid_categories = {category.value for category in AmenityCategory}
    for amenity in amenities:
        if not isinstance(amenity, dict) or set(amenity) - _ALLOWED_AMENITY_FIELDS:
            raise ValueError("amenity has an unsupported shape")
        category_value = amenity.get("category", "other")
        if not isinstance(category_value, str) or category_value.lower() not in valid_categories:
            raise ValueError(f"unsupported amenity category: {category_value}")
        phrase = amenity.get("phrase", category_value)
        if not isinstance(phrase, str):
            raise ValueError("amenity phrase must be a string")
        for boolean_field in ("wheelchair_required", "important"):
            if boolean_field in amenity and not isinstance(amenity[boolean_field], bool):
                raise ValueError(f"amenity {boolean_field} must be boolean")


class LLMParser:
    name = "llm"

    def __init__(self, store, llm_cfg: dict[str, Any]) -> None:
        self.store = store
        self.llm_cfg = llm_cfg

    def _messages(self, text: str, repair_error: str | None = None) -> list[dict[str, str]]:
        repair_instruction = (
            f"Your previous response failed validation: {repair_error}. Return corrected JSON only."
            if repair_error else ""
        )
        try:
            prompt_template = _PROMPT_PATH.read_text()
        except OSError:
            _LOG.warning("Versioned extraction prompt is unavailable: %s", _PROMPT_PATH)
            prompt_template = "Return JSON only matching this schema: {schema}. Use null when absent."
        system_message = prompt_template.replace(
            "{schema}", json.dumps(_EXTRACTION_SCHEMA, separators=(",", ":"))
        )
        user_message = f"Request: {text}\n{repair_instruction}".strip()
        return [{"role": "system", "content": system_message}, {"role": "user", "content": user_message}]

    def _call(self, text: str, repair_error: str | None = None) -> str:
        model_name = self.llm_cfg.get("model")
        if not model_name or model_name == "CHANGE_ME":
            raise RuntimeError("LLM model is not configured")
        return chat(
            self._messages(text, repair_error),
            model=model_name,
            temperature=float(self.llm_cfg.get("temperature", 0.0)),
            timeout_s=float(self.llm_cfg.get("timeout_s", 60.0)),
        )

    def _log_raw(self, request_id: str, raw_response: str) -> None:
        log_directory = repo_path("results/llm_logs")
        try:
            log_directory.mkdir(parents=True, exist_ok=True)
            (log_directory / f"{request_id}.json").write_text(
                json.dumps({"request_id": request_id, "parser": self.name, "raw_response": raw_response})
            )
        except OSError:
            _LOG.warning("Could not write LLM extraction log", exc_info=True)

    def _fallback(self, text: str, request_id: str) -> ParseResult:
        """Keep the app usable offline without inventing any safety-critical values."""
        deterministic_result = RulesParser(self.store).parse(text, request_id)
        return deterministic_result.model_copy(update={"parser": self.name})

    def _ground(self, extracted: dict[str, Any], text: str, request_id: str) -> ParseResult:
        _validate_extraction(extracted)
        origin_text = extracted.get("origin")
        destination_text = extracted.get("destination")
        vehicle_text = extracted.get("vehicle")
        request = TripRequest(
            request_id=request_id,
            raw_text=text,
            origin=self.store.geocode(origin_text) if isinstance(origin_text, str) and origin_text.strip() else None,
            destination=(
                self.store.geocode(destination_text)
                if isinstance(destination_text, str) and destination_text.strip()
                else None
            ),
            start_soc_pct=extracted.get("start_soc_pct"),
            arrival_soc_pct_min=extracted.get("arrival_soc_pct_min"),
            max_detour_min=extracted.get("max_detour_min"),
            min_power_kw=extracted.get("min_power_kw"),
            connectors_override=[_normalize_connector(value) for value in extracted.get("connectors", [])] or None,
            tradeoff_profile=detect_profile(text),
            weight_overrides=detect_weight_overrides(text),
        )
        matching_vehicles = self.store.find_vehicles(vehicle_text) if isinstance(vehicle_text, str) else []

        ambiguous: dict[str, list[str]] = {}
        if len(matching_vehicles) == 1:
            request.vehicle_id = matching_vehicles[0].vehicle_id
        elif len(matching_vehicles) > 1:
            ambiguous["vehicle_id"] = [vehicle.vehicle_id for vehicle in matching_vehicles]

        for amenity_data in extracted.get("amenities", []):
            category = AmenityCategory(amenity_data.get("category", "other").lower())
            request.amenities.append(
                AmenityPreference(
                    category=category,
                    phrase=amenity_data.get("phrase", category.value),
                    wheelchair_required=bool(amenity_data.get("wheelchair_required", False)),
                    priority=("important" if bool(amenity_data.get("important", False)) else "nice_to_have"),
                )
            )

        priority_keys = detect_priority_keys(text)
        for preference in request.amenities:
            if preference.key in priority_keys or preference.category.value in priority_keys:
                preference.priority = "important"
        missing_fields = [field for field in request.missing_safety_critical() if field not in ambiguous]
        return ParseResult(
            request=request,
            missing_fields=missing_fields,
            ambiguous_fields=ambiguous,
            parser=self.name,
        )

    def parse(self, text: str, request_id: str) -> ParseResult:
        raw_response = ""
        validation_error: str | None = None
        for attempt_number in range(2):
            try:
                raw_response = self._call(text, validation_error)
                self._log_raw(request_id, raw_response)
                extracted = _json_object(raw_response)
                return self._ground(extracted, text, request_id)
            except (json.JSONDecodeError, TypeError, ValueError, KeyError) as error:
                validation_error = str(error)
                _LOG.warning("LLM extraction attempt %d failed validation: %s", attempt_number + 1, error)
            except Exception as error:  # network/config failures become a safe incomplete request
                _LOG.warning("LLM extraction unavailable: %s", error)
                break
        if raw_response:
            self._log_raw(request_id, raw_response)
        return self._fallback(text, request_id)
