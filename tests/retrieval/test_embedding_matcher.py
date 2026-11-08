"""Sentence-BERT preference matcher (Configs B/C).

Tests without a marker use a small deterministic concept encoder, so they run in CI without torch
or a model download. Tests marked `network` load the pinned model from configs/retrieval.yaml
(needs `pip install -e ".[nlp]"` and, the first time, internet).
"""

import math
import random
import re
import sys

import numpy as np
import pytest

from clickless.config import load_config, repo_path
from clickless.contracts import Amenity, AmenityCategory, AmenityPreference, Provenance
from clickless.pipeline import build_pipeline
from clickless.retrieval.documents import amenity_document, preference_query
from clickless.retrieval.embedding_matcher import EmbeddingMatcher

_CONCEPTS = (
    ("mexican", "taco", "burrito"),
    ("burger",),
    ("food", "restaurant", "lunch", "diner", "kitchen"),
    ("coffee", "cafe", "espresso"),
    ("restroom", "toilet", "bathroom"),
    ("grocery", "supermarket"),
)


class ConceptEncoder:
    """Counts concept keywords per text and records every call (stand-in for Sentence-BERT)."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, texts):
        self.calls.append(list(texts))
        rows = []
        for text in texts:
            words = re.findall(r"[a-z]+", text.lower())
            rows.append([sum(w.startswith(k) for w in words for k in group) for group in _CONCEPTS])
        return np.array(rows, dtype=float)


def _amenity(store, amenity_id):
    return next(a for a in store.amenities if a.amenity_id == amenity_id)


def _pref(category, phrase=""):
    return AmenityPreference(category=AmenityCategory(category), phrase=phrase)


def _all_pairs(store):
    prefs = [_pref("food", "mexican food"), _pref("food", "lunch"), _pref("coffee"), _pref("restroom", "bathroom")]
    return [(p, a) for p in prefs for a in store.amenities]


def _scores(matcher, pairs):
    return {(p.phrase, p.category, a.amenity_id): matcher.score(p, a) for p, a in pairs}


def _cached_matcher(store, cache_dir, revision="r1", encoder=None):
    encoder = encoder or ConceptEncoder()
    return EmbeddingMatcher(store, "concepts", revision=revision, cache_dir=cache_dir, encoder=encoder)


# ---------------------------------------------------------------- documents and queries
def test_amenity_document_uses_descriptive_fields_only(store):
    assert amenity_document(_amenity(store, "fix:a05")) == "Verde Fixture Kitchen. food. restaurant. mexican"
    # fix:a06 carries wheelchair=yes in its tags: accessibility is planning's job, not matching's.
    assert amenity_document(_amenity(store, "fix:a06")) == "Verde Fixture Restroom. restroom. toilets"

    venue = Amenity(
        amenity_id="osm:node/1",
        name="Taco Town",
        category=AmenityCategory.FOOD,
        lat=0,
        lon=0,
        tags={"amenity": "fast_food", "cuisine": "mexican;tex_mex", "brand": "Taco Town", "opening_hours": "24/7"},
        provenance=Provenance(source="SYNTHETIC", retrieved_at="2026-11-07T00:00:00Z", status="static"),
    )
    assert amenity_document(venue) == "Taco Town. food. fast food. mexican, tex mex"


def test_preference_query_adds_missing_category_word():
    assert preference_query(_pref("food", "lunch")) == "lunch food"
    assert preference_query(_pref("food", "mexican food")) == "mexican food"
    assert preference_query(_pref("restroom", "wheelchair  accessible restroom")) == "wheelchair accessible restroom"
    assert preference_query(_pref("coffee")) == "coffee"
    assert preference_query(_pref("other", "ev museum")) == "ev museum"


# ---------------------------------------------------------------- scoring (no model needed)
def test_related_phrase_scores_higher_than_unrelated(store):
    matcher = EmbeddingMatcher(store, encoder=ConceptEncoder())
    mexican = _pref("food", "mexican food")
    coffee = _pref("coffee", "coffee")

    assert matcher.score(mexican, _amenity(store, "fix:a05")) > matcher.score(mexican, _amenity(store, "fix:a03"))
    assert matcher.score(coffee, _amenity(store, "fix:a01")) > 0.9
    assert matcher.score(coffee, _amenity(store, "fix:a04")) == 0.0  # restroom: unrelated


def test_score_is_clipped_rounded_cosine(store):
    vectors = {
        "coffee": [1.0, 0.0],
        "Fixture Coffee Bar. coffee. cafe": [2.0, 0.0],  # same direction, different length
        "Canyon Fixture Diner. food. restaurant. american": [1.0, 1.0],  # 45 degrees
        "Junction Fixture Burgers. food. fast food. burger": [-1.0, 0.0],  # opposite
        "Junction Fixture Restroom. restroom. toilets": [0.0, 3.0],  # orthogonal
    }
    matcher = EmbeddingMatcher(encoder=lambda texts: np.array([vectors[t] for t in texts]))
    coffee = _pref("coffee")

    assert matcher.score(coffee, _amenity(store, "fix:a01")) == 1.0
    assert matcher.score(coffee, _amenity(store, "fix:a02")) == round(math.cos(math.pi / 4), 4)
    assert matcher.score(coffee, _amenity(store, "fix:a03")) == 0.0  # negative cosine is clipped
    assert matcher.score(coffee, _amenity(store, "fix:a04")) == 0.0
    assert all(isinstance(matcher.score(coffee, _amenity(store, i)), float) for i in ("fix:a01", "fix:a02"))


def test_scores_do_not_depend_on_call_order(store):
    pairs = _all_pairs(store)
    shuffled = pairs[:]
    random.Random(573).shuffle(shuffled)
    first_encoder = ConceptEncoder()

    forward = _scores(EmbeddingMatcher(store, encoder=first_encoder), pairs)
    backward = _scores(EmbeddingMatcher(store, encoder=ConceptEncoder()), pairs[::-1])
    mixed = _scores(EmbeddingMatcher(store, encoder=ConceptEncoder()), shuffled)

    assert forward == backward == mixed
    # Every store amenity is embedded in one sorted batch, before any query.
    assert first_encoder.calls[0] == sorted({amenity_document(a) for a in store.amenities})


def test_disk_cache_is_reused_and_keyed_by_documents(store, tmp_path):
    pairs = _all_pairs(store)
    documents = {amenity_document(a) for a in store.amenities}
    cold = _scores(_cached_matcher(store, tmp_path), pairs)
    assert len(list(tmp_path.glob("*.npz"))) == 1

    warm_encoder = ConceptEncoder()
    assert _scores(_cached_matcher(store, tmp_path, encoder=warm_encoder), pairs) == cold
    assert not any(documents & set(call) for call in warm_encoder.calls)  # documents came from disk

    preference, amenity = pairs[0]
    smaller_store = type("Store", (), {"amenities": store.amenities[:3]})()
    _cached_matcher(smaller_store, tmp_path).score(preference, amenity)
    _cached_matcher(store, tmp_path, revision="r2").score(preference, amenity)
    assert len(list(tmp_path.glob("*.npz"))) == 3  # new document set and new revision: new files


def test_unreadable_disk_cache_is_recomputed(store, tmp_path):
    pairs = _all_pairs(store)
    expected = _scores(_cached_matcher(store, tmp_path), pairs)
    (cache_file,) = tmp_path.glob("*.npz")
    cache_file.write_bytes(b"not a zip file")

    assert _scores(_cached_matcher(store, tmp_path), pairs) == expected
    with np.load(cache_file, allow_pickle=False) as rewritten:
        assert rewritten["vectors"].shape[0] == len({amenity_document(a) for a in store.amenities})


def test_model_needs_a_pinned_revision(store):
    matcher = EmbeddingMatcher(store, "sentence-transformers/all-MiniLM-L6-v2")  # nothing loaded yet
    with pytest.raises(ValueError, match="sbert_revision"):
        matcher.score(_pref("food"), store.amenities[0])


def test_missing_nlp_extra_gives_install_hint(store, monkeypatch):
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    matcher = EmbeddingMatcher(store, "sentence-transformers/all-MiniLM-L6-v2", revision="abc")
    with pytest.raises(ImportError, match=r"\.\[nlp\]"):
        matcher.score(_pref("food"), store.amenities[0])


def test_config_b_wires_the_pinned_model(store):
    cfg_b = load_config("B")
    retrieval = cfg_b["retrieval"]

    matcher = build_pipeline(cfg_b, store).matcher

    assert isinstance(matcher, EmbeddingMatcher)
    assert re.fullmatch(r"[0-9a-f]{40}", retrieval["sbert_revision"])  # a commit, not a branch name
    assert (matcher.model_name, matcher.revision, matcher.device) == (
        retrieval["sbert_model"],
        retrieval["sbert_revision"],
        "cpu",
    )
    assert matcher.cache_dir == repo_path("data/cache/sbert")


# ---------------------------------------------------------------- the pinned model (network)
@pytest.fixture(scope="module")
def sbert_settings():
    pytest.importorskip("sentence_transformers")
    retrieval = load_config("B")["retrieval"]
    return {"model_name": retrieval["sbert_model"], "revision": retrieval["sbert_revision"]}


@pytest.mark.network
def test_sbert_related_phrase_beats_unrelated_phrase(store, sbert_settings):
    matcher = EmbeddingMatcher(store, **sbert_settings)
    kitchen, burgers = _amenity(store, "fix:a05"), _amenity(store, "fix:a03")
    coffee_bar, restroom = _amenity(store, "fix:a01"), _amenity(store, "fix:a04")

    mexican = _pref("food", "mexican food")
    assert matcher.score(mexican, kitchen) > matcher.score(mexican, burgers) + 0.1
    coffee = _pref("coffee", "somewhere to grab a coffee")
    assert matcher.score(coffee, coffee_bar) > matcher.score(coffee, restroom) + 0.2
    bathroom = _pref("restroom", "a bathroom")
    assert matcher.score(bathroom, restroom) > matcher.score(bathroom, kitchen) + 0.2


@pytest.mark.network
def test_sbert_scores_are_stable_across_order_and_cache(store, sbert_settings, tmp_path):
    pairs = _all_pairs(store)

    forward = _scores(EmbeddingMatcher(store, cache_dir=tmp_path, **sbert_settings), pairs)
    backward = _scores(EmbeddingMatcher(store, **sbert_settings), pairs[::-1])
    from_cache = _scores(EmbeddingMatcher(store, cache_dir=tmp_path, **sbert_settings), pairs)

    assert forward == backward == from_cache
    assert all(0.0 <= score <= 1.0 for score in forward.values())
