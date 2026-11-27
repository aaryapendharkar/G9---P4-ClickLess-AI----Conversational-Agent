"""Minutes-equivalent plan cost (the answer to "how are trade-offs weighted?"). Owner: Hriday.

    cost = w_drive*drive_min + w_charge*charge_min + detour_extra_weight*detour_min
           + stop_overhead_min*stops
           - sum_{requested prefs p} amenity_value_min[p] * best_match(p)
           - evidence_credit_min * evidence_coverage

Each requested preference is credited once per trip (at its best stop). All weights come from
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


def plan_cost(
    itinerary: Itinerary,
    request: TripRequest,
    tradeoffs_cfg: dict[str, Any],
    evidence_coverage: float = 0.0,
    evidence_credit_min: float = 0.0,
) -> PlanCost:
    prof = resolve_profile(tradeoffs_cfg, request)
    best = best_match_per_preference(itinerary, request)
    values = prof["amenity_value_min"]
    drive = prof["drive_min_weight"] * itinerary.total_drive_min
    charge = prof["charge_min_weight"] * itinerary.total_charge_min
    detour = prof["detour_extra_weight"] * itinerary.total_detour_min
    stops = prof["stop_overhead_min"] * len(itinerary.stops)
    amenity = sum(
        amenity_value_for_preference(
            values,
            preference_key,
        )
        * score
        for preference_key, score in best.items()
    )
    evidence = evidence_credit_min * evidence_coverage
    satisfaction = sum(best.values()) / len(best) if best else 1.0
    return PlanCost(
        profile=request.tradeoff_profile,
        drive_min=drive,
        charge_min=charge,
        detour_penalty_min=detour,
        stop_overhead_min=stops,
        amenity_credit_min=amenity,
        evidence_credit_min=evidence,
        total_min=drive + charge + detour + stops - amenity - evidence,
        amenity_satisfaction=satisfaction,
    )
