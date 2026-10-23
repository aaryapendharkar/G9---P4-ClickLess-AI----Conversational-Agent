"""Configurations B/C preference matcher: Sentence-BERT embeddings. Owner: Manas.

Same amenity documents as TF-IDF so the component-level comparison is fair (proposal §IV-A).
Cache amenity embeddings per snapshot hash in data/cache/. Model name pinned in
configs/retrieval.yaml. Install with `pip install -e ".[nlp]"`.
"""

from __future__ import annotations

from ..contracts import Amenity, AmenityPreference


class EmbeddingMatcher:
    def __init__(self, store=None, model_name: str = "") -> None:
        self.store = store
        self.model_name = model_name

    def score(self, preference: AmenityPreference, amenity: Amenity) -> float:
        raise NotImplementedError("TODO(Manas): Sentence-BERT cosine similarity")
