"""Minutes-equivalent plan cost (the answer to "how are trade-offs weighted?"). Owner: Hriday.

    cost = w_drive*drive_min + w_charge*charge_min + detour_extra_weight*detour_min
           + stop_overhead_min*stops
           - sum_{requested prefs p} amenity_value_min[p] * credit_score(best_match(p))
           - evidence_credit_min * evidence_coverage

Each requested preference is credited once per trip, using its best stop. An optional
configuration-specific policy may transform that best score before it becomes minute credit.
Without a policy, the raw matcher score is preserved. All weights come from
configs/tradeoffs.yaml (tuned on DEV only, frozen before test). Every term is kept in PlanCost so
the explanation can show the breakdown and the trade-off deltas without any re-computation.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from ..contracts import Itinerary, PlanCost, TripRequest


def resolve_profile(
    tradeoffs_cfg: dict[str, Any], request: TripRequest
) -> dict[str, Any]:
    """Active profile + per-request overrides like {"amenity_value_min.food": 25}."""
    name = request.tradeoff_profile or tradeoffs_cfg["default_profile"]
    prof = deepcopy(tradeoffs_cfg["profiles"][name])
    for key, value in request.weight_overrides.items():
        node = prof
        *parents, leaf = key.split(".")
        for p in parents:
            node = node[p]
        if leaf not in node:
            raise KeyError(f"unknown weight '{key}'")
        node[leaf] = value
    return prof


def best_match_per_preference(
    itinerary: Itinerary, request: TripRequest
) -> dict[str, float]:
    return {
        p.key: max(
            (s.satisfied_preferences.get(p.key, 0.0) for s in itinerary.stops),
            default=0.0,
        )
        for p in request.amenities
    }


def amenity_value_for_preference(
    amenity_values: Mapping[str, float],
    preference_key: str,
) -> float:
    """Return the specific amenity value or its base-category fallback.

    For example, ``restroom_wheelchair`` uses its dedicated value when
    configured and otherwise falls back to ``restroom``.
    """

    base_category = preference_key.split("_")[0]
    return amenity_values.get(
        preference_key,
        amenity_values.get(base_category, 0.0),
    )


def _validated_credit_policy(
    policy_config: Mapping[str, Any] | None,
) -> tuple[str, float | None]:
    """Return a validated policy name and its optional positive-score floor."""

    if policy_config is None:
        return "raw", None

    policy_name = policy_config.get("policy", "raw")

    if policy_name == "raw":
        return "raw", None

    if policy_name != "floor":
        raise ValueError(f"unknown amenity-credit policy: {policy_name!r}")

    if "floor" not in policy_config:
        raise ValueError("amenity-credit floor policy requires a 'floor' value")

    try:
        minimum_positive_score = float(policy_config["floor"])
    except (TypeError, ValueError) as error:
        raise ValueError(
            "amenity-credit floor must be a number between 0 and 1"
        ) from error

    if not 0.0 <= minimum_positive_score <= 1.0:
        raise ValueError("amenity-credit floor must be between 0 and 1")

    return policy_name, minimum_positive_score


def validate_amenity_credit_policy(
    policy_config: Mapping[str, Any] | None,
) -> None:
    """Fail fast when an amenity-credit configuration is invalid."""

    _validated_credit_policy(policy_config)


def apply_amenity_credit_policy(
    raw_scores: Mapping[str, float],
    policy_config: Mapping[str, Any] | None = None,
) -> dict[str, float]:
    """Transform matcher scores before converting them into minute credit.

    A missing configuration, or an explicit ``raw`` policy, preserves the
    matcher scores. Config A's ``floor`` policy raises only positive,
    category-gated TF-IDF scores to the configured minimum. A zero score stays
    zero, so an unrelated or missing amenity never earns credit.
    """

    copied_scores = dict(raw_scores)
    policy_name, minimum_positive_score = _validated_credit_policy(
        policy_config
    )

    if policy_name == "raw":
        return copied_scores

    # A validated floor policy always returns a numerical floor.
    assert minimum_positive_score is not None

    return {
        preference_key: (
            max(raw_score, minimum_positive_score) if raw_score > 0.0 else 0.0
        )
        for preference_key, raw_score in copied_scores.items()
    }


def plan_cost(
    itinerary: Itinerary,
    request: TripRequest,
    tradeoffs_cfg: dict[str, Any],
    evidence_coverage: float = 0.0,
    evidence_credit_min: float = 0.0,
    amenity_credit_config: Mapping[str, Any] | None = None,
) -> PlanCost:
    """Convert one itinerary's time, preference and evidence facts into cost."""

    profile = resolve_profile(tradeoffs_cfg, request)

    # The matcher produces the raw score. The optional policy controls how
    # that score becomes ranking credit without changing the stored match.
    raw_best_matches = best_match_per_preference(
        itinerary,
        request,
    )
    credit_scores = apply_amenity_credit_policy(
        raw_best_matches,
        amenity_credit_config,
    )

    amenity_values = profile["amenity_value_min"]

    drive_cost = profile["drive_min_weight"] * itinerary.total_drive_min
    charge_cost = profile["charge_min_weight"] * itinerary.total_charge_min
    detour_cost = profile["detour_extra_weight"] * itinerary.total_detour_min
    stop_cost = profile["stop_overhead_min"] * len(itinerary.stops)

    amenity_credit = sum(
        amenity_value_for_preference(
            amenity_values,
            preference_key,
        )
        * score
        for preference_key, score in credit_scores.items()
    )

    evidence_credit = evidence_credit_min * evidence_coverage

    amenity_satisfaction = (
        sum(credit_scores.values()) / len(credit_scores) if credit_scores else 1.0
    )

    return PlanCost(
        profile=request.tradeoff_profile,
        drive_min=drive_cost,
        charge_min=charge_cost,
        detour_penalty_min=detour_cost,
        stop_overhead_min=stop_cost,
        amenity_credit_min=amenity_credit,
        evidence_credit_min=evidence_credit,
        total_min=(
            drive_cost
            + charge_cost
            + detour_cost
            + stop_cost
            - amenity_credit
            - evidence_credit
        ),
        amenity_satisfaction=amenity_satisfaction,
    )
