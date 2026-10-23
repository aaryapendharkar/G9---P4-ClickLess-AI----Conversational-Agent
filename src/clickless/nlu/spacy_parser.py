"""Configuration A, spaCy variant (optional; proposal says "rules or spaCy"). Owner: Deepti.

Idea: spaCy GPE entities for origin/destination + EntityRuler patterns for vehicles from the
curated alias table; fall back to RulesParser for battery/detour numbers.
Install with `pip install -e ".[nlp]"` and `python -m spacy download en_core_web_sm`.
"""

from __future__ import annotations

from ..contracts import ParseResult


class SpacyParser:
    name = "spacy"

    def __init__(self, store, model: str = "en_core_web_sm") -> None:
        self.store = store
        self.model = model

    def parse(self, text: str, request_id: str) -> ParseResult:
        raise NotImplementedError("TODO(Deepti): spaCy-based extraction")
