"""Compare possible ways of turning TF-IDF matches into amenity credit.

This module is an analysis tool, not part of the live ranking pipeline.
Config A continues to use the raw TF-IDF scores in ranking/cost.py until
the team records and approves a policy decision.

Owner: Hriday.
Reviewers for the policy decision: Manas and Ian.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from ..contracts import Itinerary, TripRequest
from .cost import best_match_per_preference, plan_cost, resolve_profile

CreditPolicy = Literal["raw", "gated_full", "normalized"]

CREDIT_POLICIES: tuple[CreditPolicy, ...] = (
    "raw",
    "gated_full",
    "normalized",
)


@dataclass(frozen=True)
class PolicyItineraryCost:
    """One itinerary's ranking figures under one experimental credit policy."""

    policy: CreditPolicy
    itinerary_id: str
    amenity_credit_min: float
    amenity_satisfaction: float
    total_min: float


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
                    amenity_credit_min=amenity_credit_min,
                    amenity_satisfaction=amenity_satisfaction,
                    total_min=adjusted_total_min,
                )
            )

        # Ranking uses the same core rule as the weighted ranker: lower cost is
        # better. The itinerary ID provides deterministic ordering for a tie.
        comparison[policy] = sorted(
            policy_costs,
            key=lambda result: (result.total_min, result.itinerary_id),
        )

    return comparison


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
