"""Tests for Configuration A's logistic-regression model lifecycle."""

from pathlib import Path

import numpy as np
import pytest

from clickless.config import load_config
from clickless.contracts import Itinerary, TripRequest
from clickless.pipeline import build_pipeline
from clickless.ranking.cost import plan_cost
from clickless.ranking.logreg_ranker import LogRegRanker, extract_feature_values
from clickless.ranking.weighted_ranker import WeightedRanker


def _itinerary(
    itinerary_id: str,
    drive_min: float,
    charge_min: float,
    detour_min: float,
) -> Itinerary:
    """Build a minimal plan with known numerical ranking features."""

    return Itinerary(
        itinerary_id=itinerary_id,
        request_id="logreg-test",
        vehicle_id="fixture-vehicle",
        stops=[],
        legs=[],
        total_drive_min=drive_min,
        total_charge_min=charge_min,
        total_detour_min=detour_min,
        arrival_soc_pct=20.0,
    )


def _request() -> TripRequest:
    """Return a request with no amenities, giving satisfaction feature 1.0."""

    return TripRequest(
        request_id="logreg-test",
        raw_text="logistic regression ranker test",
    )


class _AlwaysGoodModel:
    """Small prediction stand-in for exercising the trained-model path."""

    def predict_proba(self, feature_rows):
        return np.array([[0.1, 0.9] for _feature_row in feature_rows])


def test_feature_vector_follows_documented_order(cfg_a):
    """Training and runtime prediction must use the same feature positions."""

    itinerary = _itinerary(
        "feature-order",
        drive_min=90.0,
        charge_min=10.0,
        detour_min=4.0,
    )
    request = _request()
    cost = plan_cost(
        itinerary,
        request,
        cfg_a["tradeoffs"],
    )

    assert extract_feature_values(itinerary, cost) == [
        100.0,
        0.0,
        4.0,
        10.0,
        1.0,
    ]


def test_missing_model_uses_weighted_fallback(cfg_a, tmp_path: Path):
    """A fresh checkout still ranks plans before a model is trained."""

    ranker = LogRegRanker(
        cfg_a["tradeoffs"],
        model_path=tmp_path / "missing.joblib",
    )
    request = _request()
    slower = _itinerary("slower", 180.0, 20.0, 10.0)
    faster = _itinerary("faster", 100.0, 10.0, 2.0)

    ranked = ranker.rank(
        [slower, faster],
        request,
    )

    assert ranker.fallback_used
    assert [itinerary.itinerary_id for itinerary, _cost in ranked] == [
        "faster",
        "slower",
    ]


def test_pipeline_wires_floor_policy_into_every_config_a_ranker(
    pipe_a,
    cfg_a,
    store,
):
    """Every Config A ranking path must use the same TF-IDF credit policy."""

    expected_credit_policy = cfg_a["ranking"]["tfidf_credit"]

    # Config A ranks with the weighted ranker for the final run (issue #79).
    assert isinstance(pipe_a.ranker, WeightedRanker)
    assert pipe_a.ranker.amenity_credit_config == expected_credit_policy

    # The logistic-regression path stays wired with the same policy for a DEV ablation.
    logreg_config_a = load_config("A")
    logreg_config_a["components"]["ranker"] = "logreg"
    logreg_pipe_a = build_pipeline(logreg_config_a, store)
    assert isinstance(logreg_pipe_a.ranker, LogRegRanker)
    assert logreg_pipe_a.ranker.amenity_credit_config == expected_credit_policy

    # The evidence-aware weighted ranker must use the same amenity-credit scale.
    evidence_weighted_config_a = load_config("A")
    evidence_weighted_config_a["components"]["ranker"] = "weighted+evidence"

    evidence_weighted_pipe_a = build_pipeline(
        evidence_weighted_config_a,
        store,
    )

    assert isinstance(evidence_weighted_pipe_a.ranker, WeightedRanker)
    assert (
        evidence_weighted_pipe_a.ranker.amenity_credit_config
        == expected_credit_policy
    )
    assert evidence_weighted_pipe_a.ranker.use_evidence is True

    # Config B does not define the Config A TF-IDF floor. Passing None keeps
    # its existing raw Sentence-BERT scoring behavior unchanged.
    config_b = load_config("B")

    # Sentence-BERT is unrelated to this wiring check and may not be installed
    # in a minimal test environment, so use the deterministic fixture matcher.
    config_b["components"]["matcher"] = "tfidf"

    pipe_b = build_pipeline(
        config_b,
        store,
    )

    assert isinstance(pipe_b.ranker, WeightedRanker)
    assert pipe_b.ranker.amenity_credit_config is None


def test_pipeline_rejects_tfidf_policy_with_non_tfidf_matcher(store):
    """A matcher-specific calibration must not leak into SBERT configs."""

    incompatible_config = load_config("A")
    incompatible_config["components"]["matcher"] = "sbert"

    with pytest.raises(
        ValueError,
        match="TF-IDF matcher",
    ):
        build_pipeline(
            incompatible_config,
            store,
        )


def test_pipeline_rejects_invalid_credit_policy_at_startup(store):
    """Configuration errors must fail before the first request is planned."""

    invalid_config = load_config("A")
    invalid_config["ranking"]["tfidf_credit"] = {
        "policy": "floor",
        "floor": 1.5,
    }

    with pytest.raises(
        ValueError,
        match="floor",
    ):
        build_pipeline(
            invalid_config,
            store,
        )


def test_missing_model_fallback_uses_floor_policy(
    pipe_a,
    cfg_a,
    demo_text,
    tmp_path,
):
    """The current weighted fallback must use Config A's configured floor."""

    source_result = pipe_a.run(
        demo_text,
        request_id="floor-fallback-source",
    )
    itinerary = source_result.alternatives[0].itinerary
    request = source_result.request

    ranker = LogRegRanker(
        cfg_a["tradeoffs"],
        model_path=tmp_path / "missing.joblib",
        amenity_credit_config=cfg_a["ranking"]["tfidf_credit"],
    )

    ranked = ranker.rank(
        [itinerary],
        request,
    )
    returned_cost = ranked[0][1]

    expected_cost = plan_cost(
        itinerary,
        request,
        cfg_a["tradeoffs"],
        amenity_credit_config=cfg_a["ranking"]["tfidf_credit"],
    )

    assert ranker.fallback_used
    assert returned_cost == expected_cost


def test_trained_model_path_uses_floor_policy(
    pipe_a,
    cfg_a,
    demo_text,
    tmp_path,
):
    """A future trained model must use the same score scale as its fallback."""

    source_result = pipe_a.run(
        demo_text,
        request_id="floor-trained-source",
    )
    itinerary = source_result.alternatives[0].itinerary
    request = source_result.request

    ranker = LogRegRanker(
        cfg_a["tradeoffs"],
        model_path=tmp_path / "missing.joblib",
        amenity_credit_config=cfg_a["ranking"]["tfidf_credit"],
    )
    ranker.model = _AlwaysGoodModel()

    ranked = ranker.rank(
        [itinerary],
        request,
    )
    returned_cost = ranked[0][1]

    expected_cost = plan_cost(
        itinerary,
        request,
        cfg_a["tradeoffs"],
        amenity_credit_config=cfg_a["ranking"]["tfidf_credit"],
    )

    assert not ranker.fallback_used
    assert returned_cost == expected_cost


def test_fit_saves_model_that_new_ranker_can_load(cfg_a, tmp_path: Path):
    """Training persists one complete preprocessing-and-classifier pipeline."""

    model_path = tmp_path / "models" / "logreg.joblib"
    training_features = [
        [95.0, 1.0, 2.0, 8.0, 1.0],
        [105.0, 1.0, 3.0, 10.0, 1.0],
        [185.0, 2.0, 15.0, 35.0, 1.0],
        [210.0, 3.0, 20.0, 45.0, 1.0],
    ]
    good_plan_labels = [1, 1, 0, 0]
    training_ranker = LogRegRanker(
        cfg_a["tradeoffs"],
        model_path=model_path,
    )

    training_ranker.fit(
        training_features,
        good_plan_labels,
    )

    assert model_path.exists()

    loaded_ranker = LogRegRanker(
        cfg_a["tradeoffs"],
        model_path=model_path,
    )
    request = _request()
    poor_plan = _itinerary("poor-plan", 175.0, 35.0, 15.0)
    good_plan = _itinerary("good-plan", 90.0, 8.0, 2.0)

    ranked = loaded_ranker.rank(
        [poor_plan, good_plan],
        request,
    )

    assert not loaded_ranker.fallback_used
    assert [itinerary.itinerary_id for itinerary, _cost in ranked] == [
        "good-plan",
        "poor-plan",
    ]


@pytest.mark.parametrize(
    ("training_features", "good_plan_labels", "expected_error_message"),
    [
        ([], [], "at least one"),
        ([[100.0, 1.0, 2.0, 8.0, 1.0]], [], "same number"),
        (
            [
                [100.0, 1.0, 2.0, 8.0, 1.0],
                [110.0, 1.0, 3.0, 9.0, 1.0],
            ],
            [1, 1],
            "both good and bad",
        ),
    ],
)
def test_fit_rejects_invalid_training_data(
    cfg_a,
    tmp_path: Path,
    training_features,
    good_plan_labels,
    expected_error_message,
):
    """Training errors should explain unusable labels before sklearn fails."""

    ranker = LogRegRanker(
        cfg_a["tradeoffs"],
        model_path=tmp_path / "invalid.joblib",
    )

    with pytest.raises(ValueError, match=expected_error_message):
        ranker.fit(training_features, good_plan_labels)
