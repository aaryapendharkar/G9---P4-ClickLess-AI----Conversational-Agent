"""Real TF-IDF preference matching for Configuration A (issue #156)."""

import pytest

from clickless.contracts import AmenityCategory, AmenityPreference
from clickless.retrieval.tfidf_matcher import TfidfMatcher


def _amenity(store, amenity_id):
    """Return one fixture amenity by its readable fixture identifier."""

    return next(amenity for amenity in store.amenities if amenity.amenity_id == amenity_id)


def _preference(category, phrase=""):
    return AmenityPreference(category=category, phrase=phrase)


def test_related_cuisine_phrase_outranks_other_food(store):
    matcher = TfidfMatcher(store)
    mexican_food = _preference(AmenityCategory.FOOD, "mexican food")
    mexican_kitchen = _amenity(store, "fix:a05")
    burger_shop = _amenity(store, "fix:a03")

    mexican_score = matcher.score(mexican_food, mexican_kitchen)
    burger_score = matcher.score(mexican_food, burger_shop)

    assert 0.0 < burger_score < mexican_score < 1.0


def test_same_category_scores_are_meaningful_not_binary(store):
    matcher = TfidfMatcher(store)
    burger_request = _preference(AmenityCategory.FOOD, "burger food")
    burgers = _amenity(store, "fix:a03")
    diner = _amenity(store, "fix:a02")

    burger_score = matcher.score(burger_request, burgers)
    diner_score = matcher.score(burger_request, diner)

    assert 0.0 < diner_score < burger_score < 1.0


def test_cross_category_pair_returns_zero_before_fitting(store, monkeypatch):
    matcher = TfidfMatcher(store)

    def fail_if_fitted(_documents):
        raise AssertionError("the category gate should run before TF-IDF fitting")

    monkeypatch.setattr(matcher._vectorizer, "fit_transform", fail_if_fitted)
    restroom = _preference(AmenityCategory.RESTROOM, "bathroom")
    coffee_shop = _amenity(store, "fix:a01")

    assert matcher.score(restroom, coffee_shop) == 0.0
    assert not matcher._is_fitted


def test_empty_phrase_falls_back_to_the_category_word(store):
    matcher = TfidfMatcher(store)
    food = _preference(AmenityCategory.FOOD)

    assert all(
        matcher.score(food, amenity) > 0.0 for amenity in store.amenities if amenity.category is AmenityCategory.FOOD
    )


def test_vectorizer_fits_once_and_queries_are_cached(store, monkeypatch):
    matcher = TfidfMatcher(store)
    original_fit_transform = matcher._vectorizer.fit_transform
    fit_calls = []

    def counted_fit_transform(documents):
        fit_calls.append(tuple(documents))
        return original_fit_transform(documents)

    monkeypatch.setattr(
        matcher._vectorizer,
        "fit_transform",
        counted_fit_transform,
    )
    preference = _preference(AmenityCategory.FOOD, "mexican food")

    first = matcher.score(preference, _amenity(store, "fix:a05"))
    second = matcher.score(preference, _amenity(store, "fix:a03"))
    repeated = matcher.score(preference, _amenity(store, "fix:a05"))

    assert len(fit_calls) == 1
    assert list(fit_calls[0]) == sorted(fit_calls[0])
    assert len(fit_calls[0]) == len(store.amenities)
    assert len(matcher._query_vectors) == 1
    assert repeated == first
    assert second < first


def test_scores_do_not_depend_on_call_order(store):
    preference = _preference(AmenityCategory.FOOD, "mexican food")
    amenities = [
        _amenity(store, "fix:a02"),
        _amenity(store, "fix:a03"),
        _amenity(store, "fix:a05"),
    ]

    forward_matcher = TfidfMatcher(store)
    forward = {amenity.amenity_id: forward_matcher.score(preference, amenity) for amenity in amenities}
    reverse_matcher = TfidfMatcher(store)
    reverse = {amenity.amenity_id: reverse_matcher.score(preference, amenity) for amenity in reversed(amenities)}

    assert reverse == forward


def test_ad_hoc_amenity_uses_frozen_vocabulary_without_refitting(
    store,
    monkeypatch,
):
    matcher = TfidfMatcher(store)
    preference = _preference(AmenityCategory.FOOD, "mexican food")
    fixture_kitchen = _amenity(store, "fix:a05")
    matcher.score(preference, fixture_kitchen)
    vocabulary_before = dict(matcher._vectorizer.vocabulary_)

    def fail_if_refitted(_documents):
        raise AssertionError("an ad-hoc amenity must not refit the frozen vocabulary")

    monkeypatch.setattr(matcher._vectorizer, "fit_transform", fail_if_refitted)
    ad_hoc_taco_shop = fixture_kitchen.model_copy(
        update={
            "amenity_id": "ad-hoc:tacos",
            "name": "Unseen Taco Counter",
            "tags": {"amenity": "restaurant", "cuisine": "mexican;tacos"},
        }
    )

    score = matcher.score(preference, ad_hoc_taco_shop)

    assert 0.0 < score <= 1.0
    assert matcher._vectorizer.vocabulary_ == vocabulary_before


def test_same_score_is_returned_exactly_on_repeated_calls(store):
    matcher = TfidfMatcher(store)
    preference = _preference(AmenityCategory.COFFEE, "coffee")
    coffee_shop = _amenity(store, "fix:a01")

    scores = [matcher.score(preference, coffee_shop) for _ in range(3)]

    assert scores[0] == pytest.approx(scores[1]) == pytest.approx(scores[2])
