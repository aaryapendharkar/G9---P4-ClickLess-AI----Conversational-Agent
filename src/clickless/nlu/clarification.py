"""Clarification gate (Config C). Owner: Deepti.

PLACEHOLDER: fixed templates, one question per turn, safety-critical fields first.
TODO(Deepti): ambiguity questions with options (two matching vehicles, two Springfields),
merge the user's answer into the previous TripRequest (multi-turn), and the
clarification-decision F1 / silent-assumption-rate targets in docs/ (<= 2%).
"""

from __future__ import annotations

from ..contracts import SAFETY_CRITICAL_FIELDS, ClarificationQuestion, ParseResult

_TEMPLATES = {
    "origin": "Where are you starting from?",
    "destination": "Where are you headed?",
    "vehicle_id": "Which EV are you driving (make, model and year)?",
    "start_soc_pct": "About what percent battery do you have right now?",
}


class TemplateClarifier:
    def question_for(self, parse: ParseResult) -> ClarificationQuestion:
        for field, options in parse.ambiguous_fields.items():
            return ClarificationQuestion(
                field=field, question=f"Which one did you mean for {field}?", options=options
            )
        ordered = [f for f in SAFETY_CRITICAL_FIELDS if f in parse.missing_fields]
        field = (ordered or parse.missing_fields)[0]
        return ClarificationQuestion(field=field, question=_TEMPLATES.get(field, f"Please give {field}."))
