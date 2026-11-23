"""Configuration A preference matcher: TF-IDF cosine similarity (proposal eq. 1).

TF-IDF and Sentence-BERT read the same text from ``retrieval/documents.py`` so
the Config A versus Config B comparison changes the matching method, not the
input documents.

The first same-category score fits one deterministic vocabulary over every
amenity document in the selected datastore. Documents are sorted before
fitting, so the vocabulary and vectors do not depend on which candidate
happens to be scored first. Duplicate documents remain in the corpus because
their frequency is meaningful to TF-IDF. The sparse document matrix and
transformed query vectors are then reused for the matcher's lifetime.

The category gate (ADR 0013) runs before fitting or vectorization. Amenities
in another category therefore always score 0.0 without doing TF-IDF work.
Wheelchair accessibility is intentionally absent from the text similarity;
planning owns that hard rule and treats an unknown value as inaccessible.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from ..contracts import Amenity, AmenityPreference
from .category_gate import passes_category_gate
from .documents import amenity_document, preference_query


class TfidfMatcher:
    """Score preferences against amenities using a frozen datastore vocabulary."""

    def __init__(self, store=None) -> None:
        self.store = store
        self._vectorizer = TfidfVectorizer(
            lowercase=True,
            norm="l2",
            dtype=np.float64,
        )

        # Fitting is deferred until the first pair that passes the category
        # gate. Building a Config A pipeline therefore remains inexpensive.
        self._is_fitted = False
        self._document_rows: dict[str, int] = {}
        self._document_matrix: Any | None = None
        self._query_vectors: dict[str, Any] = {}
        self._extra_document_vectors: dict[str, Any] = {}

    def score(self, preference: AmenityPreference, amenity: Amenity) -> float:
        """Return gated TF-IDF cosine similarity in the inclusive range [0, 1]."""

        if not passes_category_gate(preference, amenity):
            return 0.0

        self._fit_store_documents()
        query_vector = self._query_vector(preference_query(preference))
        document_vector = self._document_vector(amenity_document(amenity))

        # TfidfVectorizer produces L2-normalized rows, so their sparse dot
        # product is cosine similarity. Floating-point slack is clipped away.
        similarity = float(query_vector.multiply(document_vector).sum())
        return min(1.0, max(0.0, similarity))

    def _fit_store_documents(self) -> None:
        """Fit once on every datastore amenity document in sorted order."""

        if self._is_fitted:
            return

        amenities = getattr(self.store, "amenities", None) or []
        documents = sorted(amenity_document(amenity) for amenity in amenities)
        if not documents:
            raise ValueError("TF-IDF matching needs a datastore containing at least one amenity")

        self._document_matrix = self._vectorizer.fit_transform(documents)
        self._document_rows = {document: row_index for row_index, document in enumerate(documents)}
        self._is_fitted = True

    def _query_vector(self, text: str):
        """Transform a query once, then reuse its sparse vector."""

        vector = self._query_vectors.get(text)
        if vector is None:
            vector = self._query_vectors[text] = self._vectorizer.transform([text])
        return vector

    def _document_vector(self, text: str):
        """Return an indexed row, or transform an ad-hoc amenity without refitting."""

        row_index = self._document_rows.get(text)
        if row_index is not None:
            return self._document_matrix[row_index]

        vector = self._extra_document_vectors.get(text)
        if vector is None:
            vector = self._extra_document_vectors[text] = self._vectorizer.transform([text])
        return vector
