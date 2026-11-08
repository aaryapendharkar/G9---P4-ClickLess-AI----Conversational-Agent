"""Category gate shared by every preference matcher (ADR 0013).

No test here needs torch or a model: a pair that fails the gate must score 0.0 before anything
is embedded.
"""

import numpy as np
import pytest

from clickless.config import load_config
from clickless.contracts import AmenityCategory, AmenityPreference
from clickless.pipeline import build_pipeline
from clickless.retrieval.embedding_matcher import EmbeddingMatcher
from clickless.retrieval.tfidf_matcher import TfidfMatcher

PREFERENCES = [
    AmenityPreference(category=AmenityCategory.FOOD, phrase="lunch"),
    AmenityPreference(category=AmenityCategory.FOOD),
    AmenityPreference(category=AmenityCategory.COFFEE, phrase="somewhere to grab a coffee"),
    AmenityPreference(category=AmenityCategory.RESTROOM, phrase="a bathroom", wheelchair_required=True),
    AmenityPreference(category=AmenityCategory.GROCERY, phrase="snacks for the road"),
    AmenityPreference(category=AmenityCategory.LODGING, phrase="a motel"),
    AmenityPreference(category=AmenityCategory.OTHER, phrase="ev museum"),
]


def _other_category_pairs(store):
    return [(p, a) for p in PREFERENCES for a in store.amenities if a.category != p.category]


def _pairs_that_can_match(matcher, store):
    return {(i, a.amenity_id) for i, p in enumerate(PREFERENCES) for a in store.amenities if matcher.score(p, a) > 0}


@pytest.mark.parametrize("config", ["A", "B", "C"])
def test_every_configured_matcher_scores_zero_outside_the_category(config, store):
    # B and C build the real Sentence-BERT matcher. In CI (no nlp extra) any embedding would raise.
    matcher = build_pipeline(load_config(config), store).matcher

    assert all(matcher.score(p, a) == 0.0 for p, a in _other_category_pairs(store))


def test_sbert_checks_the_gate_before_embedding(store):
    def no_embedding(texts):
        raise AssertionError(f"embedded a pair that fails the category gate: {texts}")

    matcher = EmbeddingMatcher(store, encoder=no_embedding)

    assert all(matcher.score(p, a) == 0.0 for p, a in _other_category_pairs(store))


def test_tfidf_and_sbert_let_the_same_pairs_through(store):
    # Worst case for Sentence-BERT: an encoder that cannot tell texts apart, so the raw cosine is
    # 1.0 for every pair. Only the gate decides which amenities can satisfy a preference.
    same_vector = EmbeddingMatcher(store, encoder=lambda texts: np.ones((len(texts), 4)))
    same_category = {
        (i, a.amenity_id) for i, p in enumerate(PREFERENCES) for a in store.amenities if a.category == p.category
    }

    assert same_category  # the fixtures have food, coffee, restroom and grocery amenities
    assert _pairs_that_can_match(TfidfMatcher(store), store) == same_category
    assert _pairs_that_can_match(same_vector, store) == same_category
