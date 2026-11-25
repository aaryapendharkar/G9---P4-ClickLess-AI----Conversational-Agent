"""Compare possible ways of turning TF-IDF matches into amenity credit.

This module is an analysis tool, not part of the live ranking pipeline.
Config A continues to use the raw TF-IDF scores in ranking/cost.py until
the team records and approves a policy decision.

Owner: Hriday.
Reviewers for the policy decision: Manas and Ian.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from statistics import mean
from typing import Any, Literal

from ..contracts import AmenityPreference, Itinerary, Place, TripRequest
from .cost import best_match_per_preference, plan_cost, resolve_profile

CreditPolicy = Literal["raw", "gated_full", "normalized"]
PhraseSource = Literal["parsed", "category_fallback"]

CREDIT_POLICIES: tuple[CreditPolicy, ...] = (
    "raw",
    "gated_full",
    "normalized",
)


@dataclass(frozen=True)
class PolicyItineraryCost:
    """One itinerary's policy score plus policy-independent trip facts."""

    policy: CreditPolicy
    itinerary_id: str
    stop_count: int
    matched_preference_keys: tuple[str, ...]
    drive_min: float
    charge_min: float
    detour_min: float
    total_time_min: float
    extra_time_vs_fastest_min: float
    amenity_credit_min: float
    amenity_satisfaction: float
    total_min: float


@dataclass(frozen=True)
class AnalysisAmenity:
    """One gold amenity preference and the origin of its TF-IDF phrase."""

    key: str
    phrase: str
    phrase_source: PhraseSource


@dataclass(frozen=True)
class PreparedAnalysisRequest:
    """Resolved gold request plus an audit trail for its amenity phrases."""

    request: TripRequest
    amenities: tuple[AnalysisAmenity, ...]


def prepare_analysis_request(
    gold_request: TripRequest,
    parsed_request: TripRequest,
    geocode: Callable[[str], Place],
) -> PreparedAnalysisRequest:
    """Resolve a gold request and record where each TF-IDF phrase came from.

    Gold annotations control planning fields and constraints. The parsed request
    contributes only natural-language amenity phrases. If the parser misses a
    phrase, the deterministic category fallback is retained but explicitly
    labelled so the report cannot present it as the user's wording.
    """

    if gold_request.origin is None or gold_request.destination is None:
        raise ValueError("complete gold request must have origin and destination")

    prepared_preferences: list[AmenityPreference] = []
    analysis_amenities: list[AnalysisAmenity] = []

    for gold_preference in gold_request.amenities:
        parsed_match = next(
            (
                parsed_preference
                for parsed_preference in parsed_request.amenities
                if parsed_preference.key == gold_preference.key
                and parsed_preference.phrase.strip()
            ),
            None,
        )

        if parsed_match is not None:
            phrase = parsed_match.phrase
            phrase_source: PhraseSource = "parsed"
        else:
            phrase = gold_preference.category.value
            phrase_source = "category_fallback"

        prepared_preference = gold_preference.model_copy(update={"phrase": phrase})
        prepared_preferences.append(prepared_preference)
        analysis_amenities.append(
            AnalysisAmenity(
                key=prepared_preference.key,
                phrase=phrase,
                phrase_source=phrase_source,
            )
        )

    resolved_origin = geocode(gold_request.origin.text)
    resolved_destination = geocode(gold_request.destination.text)

    if not resolved_origin.resolved or not resolved_destination.resolved:
        raise ValueError("gold origin or destination is not geocoded")

    prepared_request = gold_request.model_copy(
        update={
            "origin": resolved_origin,
            "destination": resolved_destination,
            "amenities": prepared_preferences,
        }
    )

    return PreparedAnalysisRequest(
        request=prepared_request,
        amenities=tuple(analysis_amenities),
    )


def verified_candidate_itineraries(
    pipeline: Any,
    request: TripRequest,
) -> list[Itinerary]:
    """Build verified itineraries through the same stages as the pipeline.

    The analysis must compare policies over one shared candidate set. Keeping
    this preparation beside the comparison code makes it directly testable and
    prevents the command-line script from owning untested planning logic.
    """

    vehicle = pipeline.store.vehicle(request.vehicle_id)
    route = pipeline.router.route(
        request.origin,
        request.destination,
    )
    candidates = pipeline.retriever.candidates(
        route,
        request,
    )

    reset_detour_state = getattr(
        pipeline.detour_measurer,
        "reset",
        None,
    )
    if reset_detour_state is not None:
        reset_detour_state()

    for candidate in candidates:
        for nearby_amenity in candidate.amenities:
            for preference in request.amenities:
                nearby_amenity.match_scores[preference.key] = (
                    pipeline.matcher.score(
                        preference,
                        nearby_amenity.amenity,
                    )
                )

        candidate.detour_min_est = (
            pipeline.detour_measurer.detour_min(
                route,
                candidate,
            )
        )

    compatible_candidates, _rejected_candidates = (
        pipeline.filter.apply(
            candidates,
            request,
            vehicle,
        )
    )
    planned_itineraries = pipeline.planner.plan(
        request,
        vehicle,
        route,
        compatible_candidates,
    )

    return [
        itinerary
        for itinerary in planned_itineraries
        if pipeline.verifier.verify(
            itinerary,
            request,
            vehicle,
            route,
        ).ok
    ]

def apply_credit_policy(
    raw_scores_by_itinerary: Mapping[str, Mapping[str, float]],
    policy: CreditPolicy,
) -> dict[str, dict[str, float]]:
    """Return independent score copies transformed by one credit policy.

    The outer key is an itinerary ID. The inner key is a requested amenity
    key such as ``food`` or ``restroom_wheelchair``.

    Keeping this transformation pure means the experiment cannot accidentally
    modify an itinerary or affect the normal Config A planner.
    """

    copied_scores = {
        itinerary_id: dict(preference_scores)
        for itinerary_id, preference_scores in raw_scores_by_itinerary.items()
    }

    if policy == "raw":
        return copied_scores

    if policy == "gated_full":
        return {
            itinerary_id: {
                preference_key: 1.0 if score > 0.0 else 0.0
                for preference_key, score in preference_scores.items()
            }
            for itinerary_id, preference_scores in copied_scores.items()
        }

    if policy == "normalized":
        return _normalize_scores_within_request(copied_scores)

    # This protects future callers even though the CreditPolicy type already
    # helps editors and type checkers detect unsupported values.
    raise ValueError(f"unknown TF-IDF credit policy: {policy}")


def _normalize_scores_within_request(
    scores_by_itinerary: Mapping[str, Mapping[str, float]],
) -> dict[str, dict[str, float]]:
    """Scale each preference relative to its best candidate in this request.

    Example: if two food candidates score 0.2 and 0.4, they become 0.5 and 1.0.
    A preference with no positive matches remains zero instead of causing
    division by zero.
    """

    preference_keys = {
        preference_key
        for preference_scores in scores_by_itinerary.values()
        for preference_key in preference_scores
    }

    highest_score_by_preference = {
        preference_key: max(
            (
                preference_scores.get(preference_key, 0.0)
                for preference_scores in scores_by_itinerary.values()
            ),
            default=0.0,
        )
        for preference_key in preference_keys
    }

    normalized_scores: dict[str, dict[str, float]] = {}

    for itinerary_id, preference_scores in scores_by_itinerary.items():
        normalized_scores[itinerary_id] = {}

        for preference_key, score in preference_scores.items():
            highest_score = highest_score_by_preference[preference_key]
            normalized_scores[itinerary_id][preference_key] = (
                score / highest_score if highest_score > 0.0 else 0.0
            )

    return normalized_scores


def compare_policy_costs(
    itineraries: Sequence[Itinerary],
    request: TripRequest,
    tradeoffs_cfg: dict[str, Any],
) -> dict[CreditPolicy, list[PolicyItineraryCost]]:
    """Calculate how each policy would rank the same candidate itineraries.

    The normal cost function first calculates every non-amenity term exactly as
    Config A does today. The experiment then replaces only amenity credit and
    amenity satisfaction. It does not modify the request or any itinerary.
    """

    raw_scores_by_itinerary = {
        itinerary.itinerary_id: best_match_per_preference(itinerary, request)
        for itinerary in itineraries
    }

    fastest_total_time_min = min(
        (itinerary.total_time_min for itinerary in itineraries),
        default=0.0,
    )

    current_cost_by_itinerary = {
        itinerary.itinerary_id: plan_cost(
            itinerary,
            request,
            tradeoffs_cfg,
        )
        for itinerary in itineraries
    }

    active_profile = resolve_profile(tradeoffs_cfg, request)
    amenity_values = active_profile["amenity_value_min"]

    comparison: dict[CreditPolicy, list[PolicyItineraryCost]] = {}

    for policy in CREDIT_POLICIES:
        policy_scores_by_itinerary = apply_credit_policy(
            raw_scores_by_itinerary,
            policy,
        )
        policy_costs: list[PolicyItineraryCost] = []

        for itinerary in itineraries:
            itinerary_id = itinerary.itinerary_id
            policy_scores = policy_scores_by_itinerary[itinerary_id]
            current_cost = current_cost_by_itinerary[itinerary_id]
            raw_preference_scores = raw_scores_by_itinerary[itinerary_id]
            matched_preference_keys = tuple(
                sorted(
                    preference_key
                    for preference_key, score in raw_preference_scores.items()
                    if score > 0.0
                )
            )
            total_time_min = itinerary.total_time_min

            amenity_credit_min = _calculate_amenity_credit(
                policy_scores,
                amenity_values,
            )
            amenity_satisfaction = _calculate_amenity_satisfaction(
                policy_scores,
            )

            # The current total already subtracts raw amenity credit. Add that
            # raw credit back, then subtract the experimental policy's credit.
            adjusted_total_min = (
                current_cost.total_min
                + current_cost.amenity_credit_min
                - amenity_credit_min
            )

            policy_costs.append(
                PolicyItineraryCost(
                    policy=policy,
                    itinerary_id=itinerary_id,
                    stop_count=len(itinerary.stops),
                    matched_preference_keys=matched_preference_keys,
                    drive_min=itinerary.total_drive_min,
                    charge_min=itinerary.total_charge_min,
                    detour_min=itinerary.total_detour_min,
                    total_time_min=total_time_min,
                    extra_time_vs_fastest_min=(total_time_min - fastest_total_time_min),
                    amenity_credit_min=amenity_credit_min,
                    amenity_satisfaction=amenity_satisfaction,
                    total_min=adjusted_total_min,
                )
            )

        # Ranking uses the same core rule as the weighted ranker: lower cost is
        # better. The itinerary ID provides deterministic ordering for a tie.
        comparison[policy] = sorted(
            policy_costs,
            key=lambda result: (
                result.total_min,
                result.stop_count,
                result.itinerary_id,
            ),
        )

    return comparison


def summarize_policy_results(
    item_results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Summarize recommendation changes without hiding per-item evidence.

    Keeping this reusable calculation in the installed ``clickless`` package
    lets both the command-line analysis script and its tests import the same
    implementation on developer machines and in CI.
    """

    planned_items = [result for result in item_results if result["status"] == "planned"]

    amenity_records = [
        amenity for result in item_results for amenity in result.get("amenities", [])
    ]
    category_fallback_item_ids = sorted(
        {
            result["item_id"]
            for result in item_results
            if any(
                amenity["phrase_source"] == "category_fallback"
                for amenity in result.get("amenities", [])
            )
        }
    )
    category_fallback_item_id_set = set(category_fallback_item_ids)

    summary: dict[str, Any] = {
        "items_loaded": len(item_results),
        "items_planned": len(planned_items),
        "items_skipped_or_unplanned": len(item_results) - len(planned_items),
        "amenity_phrase_sources": {
            "parsed": sum(
                amenity["phrase_source"] == "parsed" for amenity in amenity_records
            ),
            "category_fallback": sum(
                amenity["phrase_source"] == "category_fallback"
                for amenity in amenity_records
            ),
        },
        "category_fallback_item_ids": category_fallback_item_ids,
    }

    if not planned_items:
        return summary

    raw_recommendations = {
        result["item_id"]: result["policies"]["raw"]["recommended"]["itinerary_id"]
        for result in planned_items
    }

    summary["policy_results"] = {}

    for policy in CREDIT_POLICIES:
        recommended_results = [
            result["policies"][policy]["recommended"]
            for result in planned_items
        ]
        changed_items = [
            result
            for result in planned_items
            if result["policies"][policy]["recommended"][
                "itinerary_id"
            ]
            != raw_recommendations[result["item_id"]]
        ]
        changed_item_ids = [
            result["item_id"]
            for result in changed_items
        ]

        changed_item_outcomes = []

        for result in changed_items:
            raw_recommendation = result["policies"]["raw"][
                "recommended"
            ]
            policy_recommendation = result["policies"][policy][
                "recommended"
            ]

            raw_matched_preferences = set(
                raw_recommendation[
                    "matched_preference_keys"
                ]
            )
            policy_matched_preferences = set(
                policy_recommendation[
                    "matched_preference_keys"
                ]
            )

            changed_item_outcomes.append(
                {
                    "item_id": result["item_id"],
                    "raw_itinerary_id": raw_recommendation[
                        "itinerary_id"
                    ],
                    "policy_itinerary_id": policy_recommendation[
                        "itinerary_id"
                    ],
                    "gained_preference_keys": sorted(
                        policy_matched_preferences
                        - raw_matched_preferences
                    ),
                    "lost_preference_keys": sorted(
                        raw_matched_preferences
                        - policy_matched_preferences
                    ),
                    "additional_time_vs_raw_min": (
                        policy_recommendation[
                            "total_time_min"
                        ]
                        - raw_recommendation[
                            "total_time_min"
                        ]
                    ),
                    "policy_extra_time_vs_fastest_min": (
                        policy_recommendation[
                            "extra_time_vs_fastest_min"
                        ]
                    ),
                    "stop_count_change_vs_raw": (
                        policy_recommendation["stop_count"]
                        - raw_recommendation["stop_count"]
                    ),
                    "policy_drive_min": policy_recommendation[
                        "drive_min"
                    ],
                    "policy_charge_min": policy_recommendation[
                        "charge_min"
                    ],
                    "policy_detour_min": policy_recommendation[
                        "detour_min"
                    ],
                }
            )

        summary["policy_results"][policy] = {
            "recommendations_changed_from_raw": len(
                changed_item_ids
            ),
            "changed_item_ids": changed_item_ids,
            "changed_items_using_category_fallback": [
                item_id
                for item_id in changed_item_ids
                if item_id in category_fallback_item_id_set
            ],
            "changed_item_outcomes": changed_item_outcomes,
            # These averages use the policy's own score scale. They are
            # descriptive only and must not be compared as objective quality.
            "mean_policy_scaled_recommended_credit_min": mean(
                result["amenity_credit_min"]
                for result in recommended_results
            ),
            "mean_policy_scaled_recommended_satisfaction": mean(
                result["amenity_satisfaction"]
                for result in recommended_results
            ),
        }

    return summary


def _calculate_amenity_credit(
    preference_scores: Mapping[str, float],
    amenity_values: Mapping[str, float],
) -> float:
    """Convert amenity scores into their configured minutes-equivalent credit."""

    return sum(
        _amenity_value_for_preference(preference_key, amenity_values) * score
        for preference_key, score in preference_scores.items()
    )


def _amenity_value_for_preference(
    preference_key: str,
    amenity_values: Mapping[str, float],
) -> float:
    """Find a specific weight, falling back to the base amenity category.

    For example, ``restroom_wheelchair`` first looks for its own configured
    value. If none exists, it falls back to the ``restroom`` value.
    """

    base_category = preference_key.split("_")[0]
    return amenity_values.get(
        preference_key,
        amenity_values.get(base_category, 0.0),
    )


def _calculate_amenity_satisfaction(
    preference_scores: Mapping[str, float],
) -> float:
    """Return the mean score, matching the existing ranking cost definition."""

    if not preference_scores:
        return 1.0

    return sum(preference_scores.values()) / len(preference_scores)
