"""Optional spaCy parser with a deterministic rules fallback. Owner: Deepti.

The rules parser remains the source of truth for safety-critical numeric fields and datastore
resolution. spaCy is used only when installed and its model is available; a missing optional
model must not make the application unusable.
"""

from __future__ import annotations

from ..contracts import ParseResult
from .rules_parser import RulesParser


class SpacyParser:
    name = "spacy"

    def __init__(self, store, model: str = "en_core_web_sm") -> None:
        self.store = store
        self.model = model
        self._rules_parser = RulesParser(store)
        self._nlp = self._load_model()

    def _load_model(self):
        """Load spaCy lazily; optional NLP dependencies never block Config A behavior."""
        try:
            import spacy
            return spacy.load(self.model)
        except (ImportError, OSError):
            return None

    def parse(self, text: str, request_id: str) -> ParseResult:
        """Parse safely using the deterministic parser, optionally benefiting from spaCy.

        Place recognition is intentionally not allowed to override the datastore-backed
        resolver. This keeps unknown places unresolved instead of inventing coordinates.
        """
        parsed_result = self._rules_parser.parse(text, request_id)
        if self._nlp is None:
            return parsed_result

        # Running the model validates that the optional dependency is usable. The current
        # contract has no entity-confidence field, so ungrounded entities are not promoted.
        self._nlp(text)
        return parsed_result
