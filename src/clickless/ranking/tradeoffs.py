"""Select meaningful Pareto-optimal trade-off alternatives.

The first ranked itinerary remains Recommended. Up to two additional plans may
be selected when each one is Pareto-optimal, improves a named presentation
axis, and differs meaningfully from Recommended.

Owner: Hriday.
"""

from __future__ import annotations

from typing import Any

from ..contracts import Alternative, Itinerary, PlanCost, TripRequest
from .cost import best_match_per_preference


def axis_values(
    itinerary: Itinerary,
    cost: PlanCost,
) -> dict[str, float]:
    """Return the comparable values used by Pareto selection."""

    return {
        "total_time_min": itinerary.total_time_min,
        "detour_min": itinerary.total_detour_min,
        "charge_min": itinerary.total_charge_min,
        "amenity_satisfaction": cost.amenity_satisfaction,
        "stops": float(len(itinerary.stops)),
        "arrival_soc_pct": itinerary.arrival_soc_pct,
    }


def dominates(
    first_values: dict[str, float],
    second_values: dict[str, float],
    axes: dict[str, str],
) -> bool:
    """Return whether the first plan Pareto-dominates the second plan.

    Dominance means the first plan is no worse on every configured axis and is
    strictly better on at least one axis.
    """

    first_is_no_worse = all(
        (
            first_values[axis] <= second_values[axis]
            if direction == "min"
            else first_values[axis] >= second_values[axis]
        )
        for axis, direction in axes.items()
    )

    first_is_strictly_better = any(
        (
            first_values[axis] < second_values[axis]
            if direction == "min"
            else first_values[axis] > second_values[axis]
        )
        for axis, direction in axes.items()
    )

    return first_is_no_worse and first_is_strictly_better


def pareto_front(
    ranked_plans: list[tuple[Itinerary, PlanCost]],
    axes: dict[str, str],
) -> list[tuple[Itinerary, PlanCost]]:
    """Return plans that are not dominated by another available plan."""

    values_by_plan = [axis_values(itinerary, cost) for itinerary, cost in ranked_plans]

    pareto_plans: list[tuple[Itinerary, PlanCost]] = []

    for plan_index, ranked_plan in enumerate(ranked_plans):
        plan_is_dominated = any(
            other_index != plan_index
            and dominates(
                values_by_plan[other_index],
                values_by_plan[plan_index],
                axes,
            )
            for other_index in range(len(ranked_plans))
        )

        if not plan_is_dominated:
            pareto_plans.append(ranked_plan)

    return pareto_plans


class TradeoffSelector:
    """Select Recommended plus meaningful axis-specific alternatives."""

    def __init__(self, tradeoffs_cfg: dict[str, Any]) -> None:
        self.presentation_config = tradeoffs_cfg["presentation"]

    def select(
        self,
        ranked_plans: list[tuple[Itinerary, PlanCost]],
        request: TripRequest,
    ) -> list[Alternative]:
        """Return the recommendation and up to two useful alternatives."""

        if not ranked_plans:
            return []

        axes = self.presentation_config["axes"]
        recommended_itinerary, recommended_cost = ranked_plans[0]
        recommended_values = axis_values(
            recommended_itinerary,
            recommended_cost,
        )
        recommended_preferences = self._met_preferences(
            recommended_itinerary,
            request,
        )

        alternatives = [
            Alternative(
                label="Recommended",
                itinerary=recommended_itinerary,
                cost=recommended_cost,
            )
        ]
        selected_itinerary_ids = {recommended_itinerary.itinerary_id}

        nondominated_plans = pareto_front(
            ranked_plans,
            axes,
        )

        for axis, label in self.presentation_config["labels"].items():
            if len(alternatives) >= self.presentation_config["max_alternatives"]:
                break

            axis_candidates = [
                (itinerary, cost)
                for itinerary, cost in nondominated_plans
                if itinerary.itinerary_id not in selected_itinerary_ids
                and not dominates(
                    recommended_values,
                    axis_values(itinerary, cost),
                    axes,
                )
                and self._improves_axis(
                    axis,
                    axis_values(itinerary, cost),
                    recommended_values,
                    axes[axis],
                )
            ]

            if not axis_candidates:
                continue

            best_itinerary, best_cost = min(
                axis_candidates,
                key=lambda plan: self._axis_selection_key(
                    plan,
                    axis,
                    axes[axis],
                ),
            )
            best_values = axis_values(
                best_itinerary,
                best_cost,
            )

            if not self._is_meaningfully_different(
                axis,
                best_values,
                recommended_values,
            ):
                continue

            met_preferences = self._met_preferences(
                best_itinerary,
                request,
            )

            alternatives.append(
                Alternative(
                    label=label,
                    itinerary=best_itinerary,
                    cost=best_cost,
                    deltas={
                        value_name: round(
                            value - recommended_values[value_name],
                            2,
                        )
                        for value_name, value in best_values.items()
                    },
                    gains=sorted(met_preferences - recommended_preferences),
                    losses=sorted(recommended_preferences - met_preferences),
                )
            )
            selected_itinerary_ids.add(best_itinerary.itinerary_id)

        return alternatives

    def _axis_selection_key(
        self,
        plan: tuple[Itinerary, PlanCost],
        axis: str,
        direction: str,
    ) -> tuple[float, float, str]:
        """Return deterministic priorities for one presentation axis."""

        itinerary, cost = plan
        value = axis_values(itinerary, cost)[axis]

        # Python's min() selects the smallest key. Maximized axes therefore use
        # a negative value so that larger satisfaction sorts first.
        preferred_axis_value = value if direction == "min" else -value

        return (
            preferred_axis_value,
            cost.total_min,
            itinerary.itinerary_id,
        )

    def _improves_axis(
        self,
        axis: str,
        candidate_values: dict[str, float],
        recommended_values: dict[str, float],
        direction: str,
    ) -> bool:
        """Return whether the candidate is actually better on this axis."""

        if direction == "min":
            return candidate_values[axis] < recommended_values[axis]

        return candidate_values[axis] > recommended_values[axis]

    def _is_meaningfully_different(
        self,
        axis: str,
        candidate_values: dict[str, float],
        recommended_values: dict[str, float],
    ) -> bool:
        """Reject changes too small to justify another driver-facing option."""

        difference = abs(candidate_values[axis] - recommended_values[axis])

        if axis == "amenity_satisfaction":
            return difference >= self.presentation_config["min_satisfaction_diff"]

        # Time, charging and detour axes are all measured in minutes, so they
        # share the configured minimum meaningful minute difference.
        return difference >= self.presentation_config["min_time_diff_min"]

    def _met_preferences(
        self,
        itinerary: Itinerary,
        request: TripRequest,
    ) -> set[str]:
        """Return requested preferences satisfied above the configured score."""

        satisfaction_threshold = self.presentation_config["satisfied_threshold"]

        return {
            preference_key
            for preference_key, score in best_match_per_preference(
                itinerary,
                request,
            ).items()
            if score >= satisfaction_threshold
        }
