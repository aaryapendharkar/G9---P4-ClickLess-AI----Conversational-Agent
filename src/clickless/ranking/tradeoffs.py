"""Trade-off alternatives: what the driver is shown (TA feedback). Owner: Hriday.

1. Pareto front over (total time, detour, charge time, amenity satisfaction).
2. "Recommended" = lowest minutes-equivalent cost under the active profile.
3. Up to 2 more alternatives, each the best plan on one axis (e.g. Fastest, Best amenities),
   only if it is on the Pareto front, is a different itinerary, and differs meaningfully
   (>= min_time_diff_min minutes or >= min_satisfaction_diff in satisfaction).
4. Deltas vs Recommended + gained/lost preferences -- the presenter turns these into sentences.
v0 implemented; TODO(Hriday): tie-breaking rules, "Least detour" when time differences are tiny,
property tests (an alternative never dominated by Recommended).
"""

from __future__ import annotations

from typing import Any

from ..contracts import Alternative, Itinerary, PlanCost, TripRequest
from .cost import best_match_per_preference


def axis_values(it: Itinerary, cost: PlanCost) -> dict[str, float]:
    return {
        "total_time_min": it.total_time_min,
        "detour_min": it.total_detour_min,
        "charge_min": it.total_charge_min,
        "amenity_satisfaction": cost.amenity_satisfaction,
        "stops": float(len(it.stops)),
        "arrival_soc_pct": it.arrival_soc_pct,
    }


def dominates(a: dict[str, float], b: dict[str, float], axes: dict[str, str]) -> bool:
    better_or_equal = all((a[k] <= b[k]) if d == "min" else (a[k] >= b[k]) for k, d in axes.items())
    strictly = any((a[k] < b[k]) if d == "min" else (a[k] > b[k]) for k, d in axes.items())
    return better_or_equal and strictly


def pareto_front(ranked: list[tuple[Itinerary, PlanCost]], axes: dict[str, str]) -> list[tuple[Itinerary, PlanCost]]:
    vals = [axis_values(it, c) for it, c in ranked]
    return [ranked[i] for i in range(len(ranked))
            if not any(dominates(vals[j], vals[i], axes) for j in range(len(ranked)) if j != i)]


class TradeoffSelector:
    def __init__(self, tradeoffs_cfg: dict[str, Any]) -> None:
        self.p = tradeoffs_cfg["presentation"]

    def select(self, ranked: list[tuple[Itinerary, PlanCost]], request: TripRequest) -> list[Alternative]:
        if not ranked:
            return []
        axes = self.p["axes"]
        rec_it, rec_cost = ranked[0]
        rec_vals = axis_values(rec_it, rec_cost)
        rec_met = self._met(rec_it, request)
        alts = [Alternative(label="Recommended", itinerary=rec_it, cost=rec_cost)]
        front = pareto_front(ranked, axes)
        for axis, label in self.p["labels"].items():
            if len(alts) >= self.p["max_alternatives"]:
                break
            sign = 1 if axes[axis] == "min" else -1
            best_it, best_cost = min(front, key=lambda x: (sign * axis_values(*x)[axis], x[1].total_min))
            if any(a.itinerary.itinerary_id == best_it.itinerary_id for a in alts):
                continue
            vals = axis_values(best_it, best_cost)
            time_diff = abs(vals["total_time_min"] - rec_vals["total_time_min"])
            sat_diff = abs(vals["amenity_satisfaction"] - rec_vals["amenity_satisfaction"])
            if time_diff < self.p["min_time_diff_min"] and sat_diff < self.p["min_satisfaction_diff"]:
                continue
            met = self._met(best_it, request)
            alts.append(Alternative(
                label=label, itinerary=best_it, cost=best_cost,
                deltas={k: round(vals[k] - rec_vals[k], 2) for k in vals},
                gains=sorted(met - rec_met), losses=sorted(rec_met - met)))
        return alts

    def _met(self, it: Itinerary, request: TripRequest) -> set[str]:
        thr = self.p["satisfied_threshold"]
        return {k for k, s in best_match_per_preference(it, request).items() if s >= thr}
