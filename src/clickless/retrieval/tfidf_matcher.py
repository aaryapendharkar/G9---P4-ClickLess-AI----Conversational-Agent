"""Configuration A preference matcher: TF-IDF cosine similarity (proposal eq. 1). Owner: Manas.

PLACEHOLDER: category equality only (1.0 / 0.0) so ranking has a signal on fixtures.
TODO(Manas):
  * documents and queries: use retrieval/documents.py (amenity_document, preference_query), the
    same text the SBERT matcher embeds, so the A-vs-B matcher comparison is fair
  * fit TfidfVectorizer on all amenity documents in the snapshot (fit ONCE, freeze vocabulary)
  * query = preference phrase (+ category word); score = cosine similarity, clipped to [0, 1]
  * keep category as a hard gate or as a feature? decide on DEV and write it in an ADR
The wheelchair rule is NOT applied here -- planning applies it (unknown never counts).
"""

from __future__ import annotations

from ..contracts import Amenity, AmenityPreference


class TfidfMatcher:
    def __init__(self, store=None) -> None:
        self.store = store

    def score(self, preference: AmenityPreference, amenity: Amenity) -> float:
        return 1.0 if amenity.category == preference.category else 0.0
