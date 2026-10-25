"""Configurations B/C parser: grounded JSON extraction through Voyager. Owner: Deepti.

The model may suggest strings and preferences, but the datastore remains authoritative for place
coordinates and vehicle ids. Missing or ambiguous safety-critical fields are preserved as such;
this parser never fills them with a default.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from ..contracts import AmenityCategory, AmenityPreference, ParseResult, TripRequest
from .priority_phrases import detect_priority_keys, detect_profile, detect_weight_overrides
from .rules_parser import RulesParser
from .voyager_client import chat

_LOG = logging.getLogger(__name__)

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
        system_message = (
            "Extract a charging-trip request. Return JSON only matching this schema: "
            f"{json.dumps(_EXTRACTION_SCHEMA, separators=(',', ':'))}. "
            "Do not infer origin, destination, vehicle, or battery values. Use null when absent. "
            "Use the driver's words for amenity phrases."
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
        log_directory = Path("results/llm_logs")
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
            tradeoff_profile=detect_profile(text),
            weight_overrides=detect_weight_overrides(text),
        )
        if isinstance(vehicle_text, str) and vehicle_text.strip():
            matching_vehicles = self.store.find_vehicles(vehicle_text)
        else:
            matching_vehicles = []

        ambiguous: dict[str, list[str]] = {}
        if len(matching_vehicles) == 1:
            request.vehicle_id = matching_vehicles[0].vehicle_id
        elif len(matching_vehicles) > 1:
            ambiguous["vehicle_id"] = [vehicle.vehicle_id for vehicle in matching_vehicles]

        for amenity_data in extracted.get("amenities", []):
            if not isinstance(amenity_data, dict):
                continue
            category_value = str(amenity_data.get("category", "other")).lower()
            try:
                category = AmenityCategory(category_value)
            except ValueError:
                category = AmenityCategory.OTHER
            phrase = str(amenity_data.get("phrase", category_value))
            request.amenities.append(
                AmenityPreference(
                    category=category,
                    phrase=phrase,
                    wheelchair_required=bool(amenity_data.get("wheelchair_required", False)),
                    priority=("important" if bool(amenity_data.get("important", False)) else "nice_to_have"),
                )
            )

        priority_keys = detect_priority_keys(text)
        for preference in request.amenities:
            if preference.key in priority_keys or preference.category.value in priority_keys:
                preference.priority = "important"
        missing_fields = request.missing_safety_critical()
        missing_fields = [field for field in missing_fields if field not in ambiguous]
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
