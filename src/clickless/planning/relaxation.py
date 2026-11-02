"""Find the lowest-penalty request relaxation that produces a feasible plan.

The relaxer proposes changes; it does not silently apply them. Connector
compatibility and physical reachability are never changed.

Owner: Hriday.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from itertools import product
from math import ceil
from typing import Any

from ..contracts import (
    Alternative,
    AmenityPreference,
    Relaxation,
    TripRequest,
)


@dataclass(frozen=True)
class _RelaxationState:
    """Count how many configured steps are applied to each relaxable field."""

    dropped_amenity_count: int
    detour_step_count: int
    arrival_soc_step_count: int
    penalty: float


class MinCostRelaxer:
    """Search request relaxations in penalty order."""

    def __init__(self, relaxation_cfg: dict[str, Any]) -> None:
        self.relaxation_config = relaxation_cfg
        self.rules_by_field = {rule["field"]: rule for rule in relaxation_cfg["order"]}

        required_fields = {
            "amenities",
            "max_detour_min",
            "arrival_soc_pct_min",
        }
        missing_fields = required_fields - self.rules_by_field.keys()

        if missing_fields:
            missing_text = ", ".join(sorted(missing_fields))
            raise ValueError(
                f"relaxation configuration is missing rules for: {missing_text}"
            )

    def find(
        self,
        request: TripRequest,
        solve: Callable[[TripRequest], list[Alternative]],
    ) -> Relaxation | None:
        """Return the cheapest feasible relaxation, or None when none works.

        The supplied solver owns planning, verification, ranking and alternative
        selection. Returning a non-empty list means the relaxed request is
        feasible.
        """

        for relaxation_state in self._ordered_states(request):
            relaxed_request, change_descriptions = self._apply_state(
                request,
                relaxation_state,
            )
            alternatives = solve(relaxed_request)

            if alternatives:
                return Relaxation(
                    changes=change_descriptions,
                    penalty=relaxation_state.penalty,
                    alternatives=alternatives,
                )

        return None

    def _ordered_states(
        self,
        request: TripRequest,
    ) -> list[_RelaxationState]:
        """Build every permitted non-empty state in deterministic cost order."""

        amenity_step_counts = range(len(request.amenities) + 1)
        detour_step_counts = range(self._maximum_detour_steps(request) + 1)
        arrival_soc_step_counts = range(self._maximum_arrival_soc_steps(request) + 1)

        relaxation_states = [
            self._build_state(
                dropped_amenity_count,
                detour_step_count,
                arrival_soc_step_count,
            )
            for (
                dropped_amenity_count,
                detour_step_count,
                arrival_soc_step_count,
            ) in product(
                amenity_step_counts,
                detour_step_counts,
                arrival_soc_step_counts,
            )
            if any(
                (
                    dropped_amenity_count,
                    detour_step_count,
                    arrival_soc_step_count,
                )
            )
        ]

        return sorted(
            relaxation_states,
            key=self._state_sort_key,
        )

    def _build_state(
        self,
        dropped_amenity_count: int,
        detour_step_count: int,
        arrival_soc_step_count: int,
    ) -> _RelaxationState:
        """Calculate the configured penalty for one combination of steps."""

        amenity_rule = self.rules_by_field["amenities"]
        detour_rule = self.rules_by_field["max_detour_min"]
        arrival_rule = self.rules_by_field["arrival_soc_pct_min"]

        penalty = (
            dropped_amenity_count * amenity_rule["penalty_per_step"]
            + detour_step_count * detour_rule["penalty_per_step"]
            + arrival_soc_step_count * arrival_rule["penalty_per_step"]
        )

        return _RelaxationState(
            dropped_amenity_count=dropped_amenity_count,
            detour_step_count=detour_step_count,
            arrival_soc_step_count=arrival_soc_step_count,
            penalty=float(penalty),
        )

    def _state_sort_key(
        self,
        state: _RelaxationState,
    ) -> tuple[float, int, int, int, int, int]:
        """Sort by penalty and then by the frozen field priority.

        When penalties tie, a state whose last changed field appears earlier in
        the configured order is attempted first.
        """

        step_counts = (
            state.dropped_amenity_count,
            state.detour_step_count,
            state.arrival_soc_step_count,
        )
        last_changed_field_index = max(
            index for index, step_count in enumerate(step_counts) if step_count > 0
        )

        return (
            state.penalty,
            last_changed_field_index,
            sum(step_counts),
            state.dropped_amenity_count,
            state.detour_step_count,
            state.arrival_soc_step_count,
        )

    def _maximum_detour_steps(
        self,
        request: TripRequest,
    ) -> int:
        """Return how many five-minute-style detour increases are allowed."""

        # There is no stated detour constraint to relax when the field is absent.
        if request.max_detour_min is None:
            return 0

        detour_rule = self.rules_by_field["max_detour_min"]
        step_min = detour_rule["step_min"]

        if step_min <= 0:
            raise ValueError("max_detour_min relaxation step must be positive")

        return int(detour_rule["max_extra_min"] // step_min)

    def _maximum_arrival_soc_steps(
        self,
        request: TripRequest,
    ) -> int:
        """Return how many arrival-target reductions are permitted."""

        # The relaxer cannot modify an unstated target because TripRequest does
        # not record which default was applied later by planning.
        if request.arrival_soc_pct_min is None:
            return 0

        arrival_rule = self.rules_by_field["arrival_soc_pct_min"]
        step_pct = arrival_rule["step_pct"]
        floor_pct = arrival_rule["floor_pct"]

        if step_pct <= 0:
            raise ValueError("arrival_soc_pct_min relaxation step must be positive")

        relaxable_percentage = max(
            0.0,
            request.arrival_soc_pct_min - floor_pct,
        )

        return ceil(relaxable_percentage / step_pct)

    def _apply_state(
        self,
        request: TripRequest,
        state: _RelaxationState,
    ) -> tuple[TripRequest, list[str]]:
        """Create a modified request and readable descriptions of its changes."""

        updated_fields: dict[str, Any] = {}
        change_descriptions: list[str] = []

        if state.dropped_amenity_count:
            remaining_amenities, amenity_changes = self._drop_amenities(
                request,
                state.dropped_amenity_count,
            )
            updated_fields["amenities"] = remaining_amenities
            change_descriptions.extend(amenity_changes)

        if state.detour_step_count:
            detour_rule = self.rules_by_field["max_detour_min"]
            original_detour_min = request.max_detour_min
            relaxed_detour_min = (
                original_detour_min + state.detour_step_count * detour_rule["step_min"]
            )

            updated_fields["max_detour_min"] = relaxed_detour_min
            change_descriptions.append(
                "max detour "
                f"{self._display_number(original_detour_min)} -> "
                f"{self._display_number(relaxed_detour_min)} min"
            )

        if state.arrival_soc_step_count:
            arrival_rule = self.rules_by_field["arrival_soc_pct_min"]
            original_arrival_soc = request.arrival_soc_pct_min
            relaxed_arrival_soc = max(
                arrival_rule["floor_pct"],
                original_arrival_soc
                - state.arrival_soc_step_count * arrival_rule["step_pct"],
            )

            updated_fields["arrival_soc_pct_min"] = relaxed_arrival_soc
            change_descriptions.append(
                "arrival charge "
                f"{self._display_number(original_arrival_soc)}% -> "
                f"{self._display_number(relaxed_arrival_soc)}%"
            )

        relaxed_request = request.model_copy(
            update=updated_fields,
            deep=True,
        )

        return relaxed_request, change_descriptions

    def _drop_amenities(
        self,
        request: TripRequest,
        drop_count: int,
    ) -> tuple[list[AmenityPreference], list[str]]:
        """Drop nice-to-have amenities before important amenities.

        Preferences with equal priority retain their original request order.
        """

        indexed_amenities = list(enumerate(request.amenities))
        drop_order = sorted(
            indexed_amenities,
            key=lambda item: (
                item[1].priority != "nice_to_have",
                item[0],
            ),
        )
        dropped_items = drop_order[:drop_count]
        dropped_indexes = {index for index, _preference in dropped_items}

        remaining_amenities = [
            preference
            for index, preference in indexed_amenities
            if index not in dropped_indexes
        ]
        change_descriptions = [
            f"drop amenity: {preference.key}" for _index, preference in dropped_items
        ]

        return remaining_amenities, change_descriptions

    @staticmethod
    def _display_number(value: float) -> str:
        """Format whole numbers without an unnecessary decimal suffix."""

        return f"{value:g}"
