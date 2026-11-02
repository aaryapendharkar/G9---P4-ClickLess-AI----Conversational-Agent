"""Tests for Configuration A's logistic-regression model lifecycle."""

from pathlib import Path

import pytest

from clickless.contracts import Itinerary, TripRequest
from clickless.ranking.cost import plan_cost
from clickless.ranking.logreg_ranker import LogRegRanker, extract_feature_values


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
