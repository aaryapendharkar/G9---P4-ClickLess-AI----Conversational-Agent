"""Partial-path labels and Pareto-frontier pruning for planner search.

This module contains only search-state comparisons. Route expansion and full
itinerary simulation remain in ``planner.py`` so the numerical planning flow is
unchanged by this readability split.

Owner: Hriday.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlanningLabel:
    """One partial path reaching a charging station.

    Metrics describe the state on arrival, before charging at the final station
    in ``stop_indices``. A later expansion can therefore calculate how much
    charging that station needs for the next leg.
    """

    stop_indices: tuple[int, ...]
    arrival_soc_pct: float
    total_drive_min: float
    total_charge_min: float
    total_detour_min: float
    satisfied_preference_keys: frozenset[str]

    @property
    def last_stop_index(self) -> int:
        return self.stop_indices[-1]


def label_dominates(
    first_label: PlanningLabel,
    second_label: PlanningLabel,
    tolerance: float = 1e-9,
) -> bool:
    """Return whether the first partial path makes the second unnecessary.

    Both labels must already represent the same final station, stop count and
    satisfied-preference signature. Higher arrival charge is better; drive,
    charging and detour minutes are minimized.
    """

    first_is_no_worse = (
        first_label.arrival_soc_pct >= second_label.arrival_soc_pct - tolerance
        and first_label.total_drive_min <= second_label.total_drive_min + tolerance
        and first_label.total_charge_min <= second_label.total_charge_min + tolerance
        and first_label.total_detour_min <= second_label.total_detour_min + tolerance
    )

    first_is_strictly_better = (
        first_label.arrival_soc_pct > second_label.arrival_soc_pct + tolerance
        or first_label.total_drive_min < second_label.total_drive_min - tolerance
        or first_label.total_charge_min < second_label.total_charge_min - tolerance
        or first_label.total_detour_min < second_label.total_detour_min - tolerance
    )

    return first_is_no_worse and first_is_strictly_better


def label_metrics_are_equivalent(
    first_label: PlanningLabel,
    second_label: PlanningLabel,
    tolerance: float = 1e-9,
) -> bool:
    """Return whether two labels have effectively identical search metrics."""

    return (
        abs(first_label.arrival_soc_pct - second_label.arrival_soc_pct) <= tolerance
        and abs(first_label.total_drive_min - second_label.total_drive_min) <= tolerance
        and abs(first_label.total_charge_min - second_label.total_charge_min)
        <= tolerance
        and abs(first_label.total_detour_min - second_label.total_detour_min)
        <= tolerance
    )


def retain_nondominated_label(
    existing_frontier: list[PlanningLabel],
    candidate_label: PlanningLabel,
) -> list[PlanningLabel]:
    """Return the frontier after considering one new partial path.

    A dominated candidate is discarded. A candidate that dominates existing
    labels replaces them. Metric-equivalent labels use the lexicographically
    smaller stop sequence so results remain deterministic.
    """

    if any(
        label_dominates(existing_label, candidate_label)
        for existing_label in existing_frontier
    ):
        return list(existing_frontier)

    equivalent_labels = [
        existing_label
        for existing_label in existing_frontier
        if label_metrics_are_equivalent(
            existing_label,
            candidate_label,
        )
    ]

    if equivalent_labels:
        deterministic_label = min(
            [
                candidate_label,
                *equivalent_labels,
            ],
            key=lambda label: label.stop_indices,
        )
        remaining_labels = [
            existing_label
            for existing_label in existing_frontier
            if existing_label not in equivalent_labels
        ]

        return sorted(
            [
                *remaining_labels,
                deterministic_label,
            ],
            key=lambda label: label.stop_indices,
        )

    remaining_labels = [
        existing_label
        for existing_label in existing_frontier
        if not label_dominates(
            candidate_label,
            existing_label,
        )
    ]

    return sorted(
        [
            *remaining_labels,
            candidate_label,
        ],
        key=lambda label: label.stop_indices,
    )
