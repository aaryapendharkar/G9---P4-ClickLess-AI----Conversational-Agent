"""Tests for the DEV-only TF-IDF amenity-credit policy comparison."""

import importlib.util
from types import SimpleNamespace

import pytest

from clickless.config import repo_path
from clickless.contracts import (
    AmenityCategory,
    AmenityPreference,
    Place,
    TripRequest,
)
from clickless.ranking.cost import (
    apply_amenity_credit_policy,
    plan_cost,
)
from clickless.ranking.tfidf_credit_analysis import (
    CREDIT_POLICIES,
    apply_credit_policy,
    compare_policy_costs,
    matcher_score_diagnostics,
    prepare_analysis_request,
    summarize_policy_results,
    verified_candidate_itineraries,
)
from clickless.ranking.weighted_ranker import WeightedRanker
from clickless.retrieval.tfidf_matcher import TfidfMatcher
from evaluation.config_names import SnapshotRequired


def _load_analysis_script():
    """Load the repository script by path on both Windows and Linux."""

    script_path = repo_path("scripts/analyze_tfidf_credit.py")
    module_spec = importlib.util.spec_from_file_location(
        "analyze_tfidf_credit_script",
        script_path,
    )

    if module_spec is None or module_spec.loader is None:
        raise ImportError(f"could not load analysis script from {script_path}")

    analysis_script = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(analysis_script)
    return analysis_script


analysis_script = _load_analysis_script()

def test_sbert_floor_derivation_uses_positive_b_c_median(
    tmp_path,
):
    """The floor comes from positive B/C DEV scores without double-counting."""

    scores_path = tmp_path / "recommended_scores.csv"
    scores_path.write_text(
        "\n".join(
            [
                (
                    "config,item_id,itinerary_id,preference_key,phrase,"
                    "n_stops,best_score,amenity_credit_min"
                ),
                "A,a-1,a-route,food,lunch,1,0.25,3.75",
                "B,b-1,b-route,food,lunch,1,0.40,6.00",
                "B,b-2,b-route,coffee,coffee,1,0.00,0.00",
                "B,b-3,b-route,restroom,restroom,1,0.60,9.00",
                "B,b-4,b-route,food,food,1,0.80,12.00",
                "C,b-1,b-route,food,lunch,1,0.40,6.00",
                "C,b-2,b-route,coffee,coffee,1,0.00,0.00",
                "C,b-3,b-route,restroom,restroom,1,0.60,9.00",
                "C,b-4,b-route,food,food,1,0.80,12.00",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    derivation = analysis_script.derive_sbert_credit_floor(scores_path)

    assert derivation["positive_score_count"] == 3
    assert derivation["mean_positive_score"] == pytest.approx(0.60)
    assert derivation["median_positive_score"] == pytest.approx(0.60)
    assert derivation["recommended_floor"] == pytest.approx(0.60)
    assert derivation["b_c_positive_scores_identical"] is True


def test_sbert_floor_derivation_rejects_different_b_c_populations(
    tmp_path,
):
    """Different B/C results require a documented population decision."""

    scores_path = tmp_path / "different_scores.csv"
    scores_path.write_text(
        "\n".join(
            [
                (
                    "config,item_id,itinerary_id,preference_key,phrase,"
                    "n_stops,best_score,amenity_credit_min"
                ),
                "B,b-1,b-route,food,lunch,1,0.40,6.00",
                "C,c-1,c-route,food,lunch,1,0.60,9.00",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="positive scores differ",
    ):
        analysis_script.derive_sbert_credit_floor(scores_path)

def _fixture_amenity(store, amenity_id):
    """Find one readable fixture amenity for matcher diagnostics."""

    return next(
        amenity for amenity in store.amenities if amenity.amenity_id == amenity_id
    )


def test_matcher_diagnostics_report_named_and_unnamed_groups(
    store,
):
    """Generated evidence must expose both document groups separately."""

    named_diner = _fixture_amenity(
        store,
        "fix:a02",
    )
    unnamed_diner = named_diner.model_copy(
        update={
            "amenity_id": "analysis:unnamed-diner",
            "name": None,
        }
    )
    diagnostic_amenities = [
        named_diner,
        unnamed_diner,
    ]
    diagnostic_store = SimpleNamespace(amenities=diagnostic_amenities)
    matcher = TfidfMatcher(diagnostic_store)

    diagnostics = matcher_score_diagnostics(
        matcher,
        diagnostic_amenities,
        queries=[
            (
                AmenityCategory.FOOD,
                "lunch",
            )
        ],
    )

    assert len(diagnostics) == 1

    lunch_diagnostic = diagnostics[0]

    assert lunch_diagnostic["category"] == "food"
    assert lunch_diagnostic["phrase"] == "lunch"
    assert lunch_diagnostic["named"]["count"] == 1
    assert lunch_diagnostic["unnamed"]["count"] == 1
    assert (
        lunch_diagnostic["unnamed"]["mean_score"]
        > lunch_diagnostic["named"]["mean_score"]
    )


def test_generic_food_phrases_fall_back_to_known_category_terms(
    store,
):
    """Out-of-vocabulary wording currently contributes only its category."""

    matcher = TfidfMatcher(store)
    diner = _fixture_amenity(store, "fix:a02")

    category_only = AmenityPreference(
        category=AmenityCategory.FOOD,
        phrase="food",
    )
    lunch = AmenityPreference(
        category=AmenityCategory.FOOD,
        phrase="lunch",
    )
    quick_bite = AmenityPreference(
        category=AmenityCategory.FOOD,
        phrase="grab a quick bite",
    )

    category_score = matcher.score(category_only, diner)
    lunch_score = matcher.score(lunch, diner)
    quick_bite_score = matcher.score(quick_bite, diner)

    assert 0.0 < category_score < 1.0
    assert lunch_score == pytest.approx(category_score)
    assert quick_bite_score == pytest.approx(category_score)


def test_unnamed_amenity_can_outscore_same_named_document(
    store,
):
    """A proper name can dilute shared category terms under L2 cosine."""

    matcher = TfidfMatcher(store)
    named_diner = _fixture_amenity(store, "fix:a02")
    unnamed_diner = named_diner.model_copy(
        update={
            "amenity_id": "analysis:unnamed-diner",
            "name": None,
        }
    )
    lunch = AmenityPreference(
        category=AmenityCategory.FOOD,
        phrase="lunch",
    )

    named_score = matcher.score(
        lunch,
        named_diner,
    )
    unnamed_score = matcher.score(
        lunch,
        unnamed_diner,
    )

    assert 0.0 < named_score < unnamed_score < 1.0


def test_prepare_analysis_request_records_parsed_and_fallback_phrases():
    """The report must distinguish user wording from category fallback text."""

    gold_request = TripRequest(
        request_id="phrase-source",
        raw_text="I want a sit-down meal and coffee",
        origin=Place(text="Origin"),
        destination=Place(text="Destination"),
        vehicle_id="test-vehicle",
        start_soc_pct=50,
        amenities=[
            AmenityPreference(category=AmenityCategory.FOOD),
            AmenityPreference(category=AmenityCategory.COFFEE),
        ],
    )
    parsed_request = TripRequest(
        request_id="phrase-source",
        raw_text=gold_request.raw_text,
        amenities=[
            AmenityPreference(
                category=AmenityCategory.COFFEE,
                phrase="coffee",
            )
        ],
    )
    resolved_places = {
        "Origin": Place(text="Origin", lat=1.0, lon=2.0),
        "Destination": Place(
            text="Destination",
            lat=3.0,
            lon=4.0,
        ),
    }

    prepared = prepare_analysis_request(
        gold_request,
        parsed_request,
        resolved_places.__getitem__,
    )

    assert prepared.request.origin.resolved
    assert prepared.request.destination.resolved
    assert [
        (
            amenity.key,
            amenity.phrase,
            amenity.phrase_source,
        )
        for amenity in prepared.amenities
    ] == [
        ("food", "food", "category_fallback"),
        ("coffee", "coffee", "parsed"),
    ]


def test_prepare_analysis_request_matches_phrase_by_category():
    """A missed wheelchair modifier must not discard the user's phrase."""

    gold_request = TripRequest(
        request_id="wheelchair-phrase",
        raw_text="I need a wheelchair accessible bathroom",
        origin=Place(text="Origin"),
        destination=Place(text="Destination"),
        vehicle_id="test-vehicle",
        start_soc_pct=50,
        amenities=[
            AmenityPreference(
                category=AmenityCategory.RESTROOM,
                wheelchair_required=True,
            )
        ],
    )
    parsed_request = TripRequest(
        request_id="wheelchair-phrase",
        raw_text=gold_request.raw_text,
        amenities=[
            AmenityPreference(
                category=AmenityCategory.RESTROOM,
                phrase="wheelchair accessible bathroom",
                wheelchair_required=False,
            )
        ],
    )
    resolved_places = {
        "Origin": Place(text="Origin", lat=1.0, lon=2.0),
        "Destination": Place(text="Destination", lat=3.0, lon=4.0),
    }

    prepared = prepare_analysis_request(
        gold_request,
        parsed_request,
        resolved_places.__getitem__,
    )

    assert prepared.amenities[0].key == "restroom_wheelchair"
    assert prepared.amenities[0].phrase == "wheelchair accessible bathroom"
    assert prepared.amenities[0].phrase_source == "parsed"


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


def test_floor_policy_reuses_production_score_transformation():
    """The experiment and live Config A policy must produce identical scores."""

    raw_scores = {
        "fast-plan": {
            "food": 0.02,
            "coffee": 0.70,
            "restroom": 0.0,
        },
        "amenity-plan": {
            "food": 0.10,
            "coffee": 0.80,
            "restroom": 0.0,
        },
    }
    floor_config = {
        "policy": "floor",
        "floor": 0.52,
    }

    transformed_scores = apply_credit_policy(
        raw_scores,
        "floor",
        floor_policy_config=floor_config,
    )

    for itinerary_id, preference_scores in raw_scores.items():
        assert transformed_scores[itinerary_id] == (
            apply_amenity_credit_policy(
                preference_scores,
                floor_config,
            )
        )


def test_floor_policy_requires_explicit_production_configuration():
    """The analysis must not hide another hard-coded floor value."""

    with pytest.raises(
        ValueError,
        match="requires Config A",
    ):
        apply_credit_policy(
            {"plan": {"food": 0.10}},
            "floor",
        )


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
    itineraries = verified_candidate_itineraries(
        pipe_a,
        request,
    )

    comparison = compare_policy_costs(
        itineraries,
        request,
        cfg_a["tradeoffs"],
        floor_policy_config=cfg_a["ranking"]["tfidf_credit"],
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


def test_raw_policy_order_matches_weighted_ranker(
    pipe_a,
    demo_text,
    cfg_a,
):
    """Every policy result must be ready to read from best to worst."""

    request = pipe_a.parser.parse(
        demo_text,
        request_id="tfidf-credit-ordering",
    ).request
    itineraries = verified_candidate_itineraries(
        pipe_a,
        request,
    )

    comparison = compare_policy_costs(
        itineraries,
        request,
        cfg_a["tradeoffs"],
        floor_policy_config=cfg_a["ranking"]["tfidf_credit"],
    )

    weighted_ranker = WeightedRanker(
        cfg_a["tradeoffs"],
    )
    live_ranked_itinerary_ids = [
        itinerary.itinerary_id
        for itinerary, _cost in weighted_ranker.rank(
            itineraries,
            request,
        )
    ]
    analysis_ranked_itinerary_ids = [
        result.itinerary_id for result in comparison["raw"]
    ]

    assert analysis_ranked_itinerary_ids == (live_ranked_itinerary_ids)


def test_policy_comparison_reports_policy_independent_trip_facts(
    pipe_a,
    demo_text,
    cfg_a,
):
    """Every policy must report the same physical facts for an itinerary."""

    request = pipe_a.parser.parse(
        demo_text,
        request_id="tfidf-credit-neutral-facts",
    ).request
    itineraries = verified_candidate_itineraries(
        pipe_a,
        request,
    )

    comparison = compare_policy_costs(
        itineraries,
        request,
        cfg_a["tradeoffs"],
        floor_policy_config=cfg_a["ranking"]["tfidf_credit"],
    )

    for itinerary in itineraries:
        results_for_itinerary = [
            next(
                result
                for result in comparison[policy]
                if result.itinerary_id == itinerary.itinerary_id
            )
            for policy in CREDIT_POLICIES
        ]
        first_result = results_for_itinerary[0]

        assert all(
            result.stop_count == first_result.stop_count
            for result in results_for_itinerary
        )
        assert all(
            result.matched_preference_keys == first_result.matched_preference_keys
            for result in results_for_itinerary
        )
        assert all(
            result.total_time_min == pytest.approx(first_result.total_time_min)
            for result in results_for_itinerary
        )
        assert first_result.total_time_min == pytest.approx(
            itinerary.total_drive_min + itinerary.total_charge_min
        )

    fastest_extra_time = min(
        result.extra_time_vs_fastest_min for result in comparison["raw"]
    )
    assert fastest_extra_time == pytest.approx(0.0)


def test_dev_summary_reports_recommendation_changes_and_unplanned_items():
    """The report must expose policy changes instead of hiding them in averages."""

    item_results = [
        {
            "item_id": "planned-change",
            "status": "planned",
            "amenities": [
                {
                    "key": "food",
                    "phrase": "food",
                    "phrase_source": "category_fallback",
                }
            ],
            "policies": {
                "raw": {
                    "recommended": {
                        "itinerary_id": "direct",
                        "stop_count": 0,
                        "matched_preference_keys": [],
                        "drive_min": 50.0,
                        "charge_min": 0.0,
                        "detour_min": 0.0,
                        "total_time_min": 50.0,
                        "extra_time_vs_fastest_min": 0.0,
                        "amenity_credit_min": 1.0,
                        "amenity_satisfaction": 0.1,
                    }
                },
                "floor": {
                    "recommended": {
                        "itinerary_id": "food-stop",
                        "stop_count": 1,
                        "matched_preference_keys": ["food"],
                        "drive_min": 52.0,
                        "charge_min": 4.0,
                        "detour_min": 2.0,
                        "total_time_min": 56.0,
                        "extra_time_vs_fastest_min": 6.0,
                        "amenity_credit_min": 6.75,
                        "amenity_satisfaction": 0.45,
                    }
                },
                "gated_full": {
                    "recommended": {
                        "itinerary_id": "food-stop",
                        "stop_count": 1,
                        "matched_preference_keys": ["food"],
                        "drive_min": 52.0,
                        "charge_min": 4.0,
                        "detour_min": 2.0,
                        "total_time_min": 56.0,
                        "extra_time_vs_fastest_min": 6.0,
                        "amenity_credit_min": 15.0,
                        "amenity_satisfaction": 1.0,
                    }
                },
                "normalized": {
                    "recommended": {
                        "itinerary_id": "food-stop",
                        "stop_count": 1,
                        "matched_preference_keys": ["food"],
                        "drive_min": 52.0,
                        "charge_min": 4.0,
                        "detour_min": 2.0,
                        "total_time_min": 56.0,
                        "extra_time_vs_fastest_min": 6.0,
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
                        "stop_count": 1,
                        "matched_preference_keys": ["coffee"],
                        "drive_min": 40.0,
                        "charge_min": 10.0,
                        "detour_min": 1.0,
                        "total_time_min": 50.0,
                        "extra_time_vs_fastest_min": 0.0,
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

    summary = summarize_policy_results(item_results)

    assert summary["items_loaded"] == 3
    assert summary["items_planned"] == 2
    assert summary["items_skipped_or_unplanned"] == 1
    assert summary["n_errors"] == 0
    assert summary["amenity_phrase_sources"] == {
        "parsed": 0,
        "category_fallback": 1,
    }
    assert summary["category_fallback_item_ids"] == ["planned-change"]

    gated_summary = summary["policy_results"]["gated_full"]
    floor_summary = summary["policy_results"]["floor"]

    assert floor_summary["recommendations_changed_from_raw"] == 1
    assert floor_summary["changed_item_ids"] == ["planned-change"]

    assert gated_summary["recommendations_changed_from_raw"] == 1
    assert gated_summary["changed_item_ids"] == ["planned-change"]
    assert gated_summary["changed_items_using_category_fallback"] == ["planned-change"]

    changed_outcome = gated_summary["changed_item_outcomes"][0]

    assert changed_outcome == {
        "item_id": "planned-change",
        "raw_itinerary_id": "direct",
        "policy_itinerary_id": "food-stop",
        "gained_preference_keys": ["food"],
        "lost_preference_keys": [],
        "additional_time_vs_raw_min": 6.0,
        "policy_extra_time_vs_fastest_min": 6.0,
        "stop_count_change_vs_raw": 1,
        "policy_drive_min": 52.0,
        "policy_charge_min": 4.0,
        "policy_detour_min": 2.0,
    }


def test_dev_summary_counts_errors_separately():
    """Analysis failures must not be hidden among expected skipped items."""

    item_results = [
        {
            "item_id": "incomplete",
            "status": "skipped_incomplete",
        },
        {
            "item_id": "failed",
            "status": "error",
            "error_type": "RuntimeError",
            "error": "synthetic failure",
        },
    ]

    summary = summarize_policy_results(item_results)

    assert summary["items_loaded"] == 2
    assert summary["items_planned"] == 0
    assert summary["items_skipped_or_unplanned"] == 1
    assert summary["n_errors"] == 1


def test_run_analysis_refuses_dev_on_fixture_data(
    monkeypatch,
    tmp_path,
):
    """The frozen DEV split must never run against fixture data."""

    monkeypatch.delenv(
        "CLICKLESS_DATA_SOURCE",
        raising=False,
    )
    monkeypatch.setattr(
        analysis_script,
        "build_pipeline",
        lambda _config: pytest.fail(
            "pipeline construction must happen after the snapshot guard"
        ),
    )

    with pytest.raises(
        SnapshotRequired,
        match="snapshot",
    ):
        analysis_script.run_analysis(
            output_path=tmp_path / "report.json",
            limit=1,
        )


def test_main_returns_nonzero_when_analysis_records_errors(
    monkeypatch,
    tmp_path,
):
    """Automation must be able to detect an incomplete analysis run."""

    output_path = tmp_path / "report.json"

    monkeypatch.setattr(
        analysis_script,
        "run_analysis",
        lambda output_path, limit: {
            "summary": {
                "n_errors": 1,
            }
        },
    )

    exit_code = analysis_script.main(
        [
            "--out",
            str(output_path),
            "--limit",
            "1",
        ]
    )

    assert exit_code == 1
