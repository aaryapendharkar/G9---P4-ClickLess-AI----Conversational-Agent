"""Only amenities of a requested category are reported under "Nearby (requested)" (ADR 0013)."""

import numpy as np
import pytest

from clickless.config import load_config
from clickless.pipeline import build_pipeline
from clickless.retrieval.embedding_matcher import EmbeddingMatcher
from clickless.retrieval.tfidf_matcher import TfidfMatcher

NEARBY = "  Nearby (requested): "


def _reported_amenities(result, store):
    """Check every reported amenity has a requested category; return the names the explanation lists."""
    assert result.status == "ok"
    requested = {preference.category for preference in result.request.amenities}
    by_id = {amenity.amenity_id: amenity for amenity in store.amenities}
    by_name = {amenity.name: amenity for amenity in store.amenities}

    for alternative in result.alternatives:
        for stop in alternative.itinerary.stops:
            categories = {by_id[amenity_id].category for amenity_id in stop.amenity_ids}
            assert categories <= requested, (alternative.label, stop.station_id, stop.amenity_ids)

    listed = [
        name
        for line in result.explanation.splitlines()
        if line.startswith(NEARBY)
        for name in line.removeprefix(NEARBY).split(", ")
    ]
    assert {by_name[name].category for name in listed} <= requested
    return listed


def test_unrelated_amenity_is_not_reported_as_requested(store, demo_text):
    # The demo asks for lunch and a wheelchair-accessible restroom. Worst case for Config B: an
    # encoder that cannot tell texts apart, so the raw cosine is 1.0 for every pair. Without the
    # category gate, Anthem's wheelchair-accessible coffee bar would count as both.
    pipe_b = build_pipeline(load_config("B"), store)
    pipe_b.matcher = EmbeddingMatcher(store, encoder=lambda texts: np.ones((len(texts), 8)))

    listed = _reported_amenities(pipe_b.run(demo_text, request_id="gate-b"), store)

    assert listed == ["Verde Fixture Kitchen", "Verde Fixture Restroom"]
    assert "Fixture Coffee Bar" not in listed


def test_config_a_reports_only_requested_amenities(pipe_a, store, demo_text):
    listed = _reported_amenities(pipe_a.run(demo_text, request_id="gate-a"), store)

    assert listed == ["Verde Fixture Kitchen", "Verde Fixture Restroom"]


def test_config_a_carries_real_tfidf_scores_into_the_plan(pipe_a, store, demo_text):
    """Planning keeps Config A's cosine scores instead of a binary category match."""

    result = pipe_a.run(demo_text, request_id="tfidf-a")
    shown_stop = result.alternatives[0].itinerary.stops[0]
    amenity_by_id = {amenity.amenity_id: amenity for amenity in store.amenities}
    shown_amenities = [amenity_by_id[amenity_id] for amenity_id in shown_stop.amenity_ids]

    assert isinstance(pipe_a.matcher, TfidfMatcher)

    # The pipeline scores nearby amenities before planning. Recomputing the
    # best score through the same public matcher interface proves the planner
    # preserved that value in ChargingStop.satisfied_preferences.
    for preference in result.request.amenities:
        expected_best_score = max(
            pipe_a.matcher.score(preference, amenity)
            for amenity in shown_amenities
        )
        recorded_score = shown_stop.satisfied_preferences[preference.key]

        assert recorded_score == pytest.approx(expected_best_score)
        assert 0.0 < recorded_score < 1.0


@pytest.mark.network
def test_config_b_reports_only_requested_amenities_with_the_pinned_model(store, demo_text, tmp_path):
    pytest.importorskip("sentence_transformers")
    cfg_b = load_config("B")
    cfg_b["retrieval"]["sbert_cache_dir"] = str(tmp_path)  # keep test embeddings out of data/cache

    listed = _reported_amenities(build_pipeline(cfg_b, store).run(demo_text, request_id="gate-b-sbert"), store)

    assert "Fixture Coffee Bar" not in listed
