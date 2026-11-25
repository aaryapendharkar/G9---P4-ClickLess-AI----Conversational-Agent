"""Tests for the DEV-only TF-IDF amenity-credit policy comparison."""

import pytest

from clickless.ranking.cost import plan_cost
from clickless.ranking.tfidf_credit_analysis import (
    CREDIT_POLICIES,
    apply_credit_policy,
    compare_policy_costs,
)
from scripts.analyze_tfidf_credit import _summarize


def test_raw_policy_preserves_original_scores_without_mutating_input():
    """Raw credit must reproduce Config A's current TF-IDF behavior."""

    original_scores = {
        "fast-plan": {"food": 0.0, "coffee": 0.20},
        "amenity-plan": {"food": 0.04, "coffee": 0.40},
    }

    transformed_scores = apply_credit_policy(original_scores, "raw")

    assert transformed_scores == original_scores
    assert transformed_scores is not original_scores

    transformed_scores["amenity-plan"]["food"] = 1.0
    assert original_scores["amenity-plan"]["food"] == pytest.approx(0.04)


def test_gated_full_policy_grants_full_credit_only_to_positive_matches():
    """A category-gated positive match becomes one; no match remains zero."""

    raw_scores = {
        "fast-plan": {"food": 0.0, "coffee": 0.20},
        "amenity-plan": {"food": 0.04, "coffee": 0.40},
    }

    transformed_scores = apply_credit_policy(raw_scores, "gated_full")

    assert transformed_scores == {
        "fast-plan": {"food": 0.0, "coffee": 1.0},
        "amenity-plan": {"food": 1.0, "coffee": 1.0},
    }


def test_normalized_policy_scales_each_preference_against_its_best_candidate():
    """Normalization compares food with food and coffee with coffee."""

    raw_scores = {
        "fast-plan": {
            "food": 0.02,
            "coffee": 0.20,
            "restroom": 0.0,
        },
        "amenity-plan": {
            "food": 0.04,
            "coffee": 0.40,
            "restroom": 0.0,
        },
    }

    transformed_scores = apply_credit_policy(raw_scores, "normalized")

    assert transformed_scores["fast-plan"]["food"] == pytest.approx(0.5)
    assert transformed_scores["amenity-plan"]["food"] == pytest.approx(1.0)

    assert transformed_scores["fast-plan"]["coffee"] == pytest.approx(0.5)
    assert transformed_scores["amenity-plan"]["coffee"] == pytest.approx(1.0)

    # Both candidates have zero restroom similarity, so neither receives credit.
    assert transformed_scores["fast-plan"]["restroom"] == 0.0
    assert transformed_scores["amenity-plan"]["restroom"] == 0.0


def test_normalized_policy_handles_preferences_missing_from_some_itineraries():
    """A candidate without a recorded preference score behaves like zero."""

    raw_scores = {
        "no-food-plan": {"coffee": 0.30},
        "food-plan": {"food": 0.10, "coffee": 0.60},
    }

    transformed_scores = apply_credit_policy(raw_scores, "normalized")

    assert "food" not in transformed_scores["no-food-plan"]
    assert transformed_scores["food-plan"]["food"] == pytest.approx(1.0)
    assert transformed_scores["no-food-plan"]["coffee"] == pytest.approx(0.5)


def test_policy_comparison_preserves_current_raw_cost(pipe_a, demo_text, cfg_a):
    """The raw experiment must exactly reproduce today's ranking cost."""

    request = pipe_a.parser.parse(
        demo_text,
        request_id="tfidf-credit-raw",
    ).request
    vehicle = pipe_a.store.vehicle(request.vehicle_id)
    route = pipe_a.router.route(request.origin, request.destination)
    candidates = pipe_a.retriever.candidates(route, request)

    for candidate in candidates:
        candidate.detour_min_est = pipe_a.router.detour_min(route, candidate)

        for nearby_amenity in candidate.amenities:
            for preference in request.amenities:
                nearby_amenity.match_scores[preference.key] = pipe_a.matcher.score(
                    preference,
                    nearby_amenity.amenity,
                )

    filtered_candidates, _notes = pipe_a.filter.apply(
        candidates,
        request,
        vehicle,
    )
    itineraries = pipe_a.planner.plan(
        request,
        vehicle,
        route,
        filtered_candidates,
    )

    comparison = compare_policy_costs(
        itineraries,
        request,
        cfg_a["tradeoffs"],
    )

    raw_results_by_itinerary = {
        result.itinerary_id: result for result in comparison["raw"]
    }

    for itinerary in itineraries:
        current_cost = plan_cost(
            itinerary,
            request,
            cfg_a["tradeoffs"],
        )
        experimental_cost = raw_results_by_itinerary[itinerary.itinerary_id]

        assert experimental_cost.amenity_credit_min == pytest.approx(
            current_cost.amenity_credit_min
        )
        assert experimental_cost.amenity_satisfaction == pytest.approx(
            current_cost.amenity_satisfaction
        )
        assert experimental_cost.total_min == pytest.approx(current_cost.total_min)


def test_policy_comparison_orders_every_policy_by_total_cost(
    pipe_a,
    demo_text,
    cfg_a,
):
    """Every policy result must be ready to read from best to worst."""

    request = pipe_a.parser.parse(
        demo_text,
        request_id="tfidf-credit-ordering",
    ).request
    vehicle = pipe_a.store.vehicle(request.vehicle_id)
    route = pipe_a.router.route(request.origin, request.destination)
    candidates = pipe_a.retriever.candidates(route, request)

    for candidate in candidates:
        candidate.detour_min_est = pipe_a.router.detour_min(route, candidate)

        for nearby_amenity in candidate.amenities:
            for preference in request.amenities:
                nearby_amenity.match_scores[preference.key] = pipe_a.matcher.score(
                    preference,
                    nearby_amenity.amenity,
                )

    filtered_candidates, _notes = pipe_a.filter.apply(
        candidates,
        request,
        vehicle,
    )
    itineraries = pipe_a.planner.plan(
        request,
        vehicle,
        route,
        filtered_candidates,
    )

    comparison = compare_policy_costs(
        itineraries,
        request,
        cfg_a["tradeoffs"],
    )

    for policy_results in comparison.values():
        total_costs = [result.total_min for result in policy_results]
        assert total_costs == sorted(total_costs)


def test_dev_summary_reports_recommendation_changes_and_unplanned_items():
    """The report must expose policy changes instead of hiding them in averages."""

    item_results = [
        {
            "item_id": "planned-change",
            "status": "planned",
            "policies": {
                "raw": {
                    "recommended": {
                        "itinerary_id": "direct",
                        "amenity_credit_min": 1.0,
                        "amenity_satisfaction": 0.1,
                    }
                },
                "gated_full": {
                    "recommended": {
                        "itinerary_id": "food-stop",
                        "amenity_credit_min": 15.0,
                        "amenity_satisfaction": 1.0,
                    }
                },
                "normalized": {
                    "recommended": {
                        "itinerary_id": "food-stop",
                        "amenity_credit_min": 15.0,
                        "amenity_satisfaction": 1.0,
                    }
                },
            },
        },
        {
            "item_id": "planned-stable",
            "status": "planned",
            "policies": {
                policy: {
                    "recommended": {
                        "itinerary_id": "same-stop",
                        "amenity_credit_min": 5.0,
                        "amenity_satisfaction": 0.5,
                    }
                }
                for policy in CREDIT_POLICIES
            },
        },
        {
            "item_id": "incomplete",
            "status": "skipped_incomplete",
        },
    ]

    summary = _summarize(item_results)

    assert summary["items_loaded"] == 3
    assert summary["items_planned"] == 2
    assert summary["items_skipped_or_unplanned"] == 1

    gated_summary = summary["policy_results"]["gated_full"]
    assert gated_summary["recommendations_changed_from_raw"] == 1
    assert gated_summary["changed_item_ids"] == ["planned-change"]
