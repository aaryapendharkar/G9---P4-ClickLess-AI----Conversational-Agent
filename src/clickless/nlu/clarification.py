"""Deterministic clarification questions for Config C. Owner: Deepti.

This module handles the question-selection and presentation part of clarification only. Multi-turn
session storage and answer merging are tracked in TODO(#53) and are intentionally outside this MVP.
It never invents a safety-critical value. A future session layer can reuse these questions when it
merges a user's answer into the previous request.
"""

from __future__ import annotations

from ..contracts import SAFETY_CRITICAL_FIELDS, ClarificationQuestion, ParseResult

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


def _field_label(field: str) -> str:
    return _FIELD_LABELS.get(field, field.replace("_", " "))


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

        # A ParseResult should not request clarification without one of these fields. Keep the
        # failure explicit instead of indexing an empty list and producing an opaque error.
        raise ValueError("cannot create a clarification question without a missing or ambiguous field")
