"""Configuration A preference matcher: TF-IDF cosine similarity (proposal eq. 1). Owner: Manas.

Category gate (retrieval/category_gate.py, ADR 0013, shared with Sentence-BERT): an amenity of
another category scores 0.0. Text similarity only ranks amenities inside the category.

PLACEHOLDER: only the gate is in place (1.0 inside the category) so ranking has a signal on fixtures.
TODO(Manas):
  * documents and queries: use retrieval/documents.py (amenity_document, preference_query), the
    same text the SBERT matcher embeds, so the A-vs-B matcher comparison is fair
  * fit TfidfVectorizer on all amenity documents in the snapshot (fit ONCE, freeze vocabulary)
  * query = preference phrase (+ category word); score = cosine similarity, clipped to [0, 1],
    returned only for amenities that pass the category gate
The wheelchair rule is NOT applied here -- planning applies it (unknown never counts).
"""

from __future__ import annotations

from ..contracts import Amenity, AmenityPreference
from .category_gate import passes_category_gate


class TfidfMatcher:
    def __init__(self, store=None) -> None:
        self.store = store

    def score(self, preference: AmenityPreference, amenity: Amenity) -> float:
        return 1.0 if passes_category_gate(preference, amenity) else 0.0
