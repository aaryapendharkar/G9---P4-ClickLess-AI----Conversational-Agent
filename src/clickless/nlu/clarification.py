"""Clarification questions and answer merging for Config C. Owner: Deepti.

Question selection is stateless. ``merge_answer`` is also pure: it updates only the field the
current question asks about and leaves every other parsed value untouched. Session state belongs
to the pipeline and is intentionally not stored in these NLU helpers (ADR 0014).
"""

from __future__ import annotations

import re

from ..contracts import SAFETY_CRITICAL_FIELDS, ClarificationQuestion, ParseResult
from ..data import DataStore

_FIELD_LABELS = {
    "origin": "starting location",
    "destination": "destination",
    "vehicle_id": "vehicle",
    "start_soc_pct": "current battery percentage",
}
_MISSING_TEMPLATES = {
    "origin": "Where are you starting from? Please include the city and state if it could be ambiguous.",
    "destination": "Where are you headed? Please include the city and state if it could be ambiguous.",
    "vehicle_id": "Which EV are you driving? Please include the make, model, and year.",
    "start_soc_pct": "What percentage battery do you have right now?",
}
_EXPLICIT_PERCENT = re.compile(r"^\s*(\d{1,3})(?:\s*%|\s+percent)?\s*$", re.IGNORECASE)


def _field_label(field: str) -> str:
    return _FIELD_LABELS.get(field, field.replace("_", " "))


def _selected_option(options: list[str], answer: str) -> str | None:
    normalized_answer = answer.strip()
    if normalized_answer.isdigit():
        option_index = int(normalized_answer) - 1
        if 0 <= option_index < len(options):
            return options[option_index]
    for option in options:
        if normalized_answer.casefold() == option.casefold():
            return option
    return None


def _replace_request(parse: ParseResult, **changes) -> ParseResult:
    updated_request = parse.request.model_copy(update=changes)
    missing_fields = [field for field in parse.missing_fields if field not in changes]
    ambiguous_fields = {
        field: options for field, options in parse.ambiguous_fields.items() if field not in changes
    }
    return parse.model_copy(
        update={
            "request": updated_request,
            "missing_fields": missing_fields,
            "ambiguous_fields": ambiguous_fields,
        }
    )


def merge_answer(
    parse: ParseResult,
    question: ClarificationQuestion,
    answer: str,
    store: DataStore,
) -> ParseResult:
    """Apply one answer to only the active clarification field.

    Invalid or vague answers return the original ParseResult so the pipeline can ask the same
    question again. Safety-critical values are never inferred from unrelated answer text.
    """
    field = question.field
    selected_option = _selected_option(question.options, answer) if question.options else None

    if field in {"origin", "destination"}:
        place_text = selected_option or answer.strip()
        place = store.geocode(place_text)
        if not place.resolved:
            return parse
        return _replace_request(parse, **{field: place})

    if field == "vehicle_id":
        if selected_option:
            return _replace_request(parse, vehicle_id=selected_option)
        matches = store.find_vehicles(answer, among=question.options or None)
        if len(matches) != 1:
            return parse
        return _replace_request(parse, vehicle_id=matches[0].vehicle_id)

    if field == "start_soc_pct":
        match = _EXPLICIT_PERCENT.fullmatch(answer)
        if not match:
            return parse
        battery_percentage = float(match.group(1))
        if not 0 <= battery_percentage <= 100:
            return parse
        return _replace_request(parse, start_soc_pct=battery_percentage)

    # Only the four safety-critical fields are currently eligible for continuation.
    if field not in SAFETY_CRITICAL_FIELDS:
        return parse
    return parse


class TemplateClarifier:
    """Choose the highest-priority missing or ambiguous safety-critical field."""

    def question_for(self, parse: ParseResult) -> ClarificationQuestion:
        for field in SAFETY_CRITICAL_FIELDS:
            options = parse.ambiguous_fields.get(field)
            if options:
                label = _field_label(field)
                return ClarificationQuestion(
                    field=field,
                    question=f"Which {label} did you mean?",
                    options=options,
                )

        for field in SAFETY_CRITICAL_FIELDS:
            if field in parse.missing_fields:
                return ClarificationQuestion(
                    field=field,
                    question=_MISSING_TEMPLATES.get(field, f"Please provide your {field.replace('_', ' ')}."),
                )

        raise ValueError("cannot create a clarification question without a missing or ambiguous field")
