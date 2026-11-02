"""Construct feasible EV itineraries. Owner: Hriday.

The planner performs a forward label-setting search over compatible corridor
stations. A label records one useful partial stop sequence. Labels only move
toward the destination, so the search cannot revisit a station or form a cycle.

The search supports any number of stops up to the number of corridor candidates;
it is not capped at two stops. Labels that reach the same station with the same
stop count and satisfied-preference signature retain a small Pareto frontier.
A label is removed only when another arrives with at least as much charge and no
more driving, charging, or detour time. Equivalent labels use the stop sequence
as a deterministic tie-breaker. Complete itineraries are built by
``itinerary_simulation.py`` and independently verified after the search. Shared
route calculations live in ``route_points.py`` so search and simulation use
identical formulas.

Normal planning uses the documented nominal efficiency. ``plan_robust`` is a
separate sensitivity run using ``efficiency_penalty_robust``; it never silently
changes the driver's recommendation.

TODO(Hriday): charge-target policy: "just enough" vs "top up while eating"
for the comfort profile.
"""

from __future__ import annotations

from typing import Any

from ..contracts import (
    Candidate,
    Itinerary,
    Route,
    TripRequest,
    Vehicle,
)
from .charging_time import estimate_charge_min
from .energy import energy_kwh, kwh_to_soc_pct
from .filters import compatible_ports
from .itinerary_simulation import satisfied_preferences, simulate_itinerary
from .label_search import PlanningLabel, retain_nondominated_label
from .route_points import (
    RoutePoint,
    candidate_route_point,
    leg_distance_km,
    leg_drive_min,
    required_departure_soc_pct,
    route_distance_scale,
)


class ItineraryPlanner:
    """Search stop sequences and orchestrate final itinerary simulation."""

    def __init__(self, energy_config: dict[str, Any]) -> None:
        self.energy_config = energy_config

    def plan(
        self,
        request: TripRequest,
        vehicle: Vehicle,
        route: Route,
        candidates: list[Candidate],
    ) -> list[Itinerary]:
        """Return every representative stop sequence that passes simulation."""

        return self._plan_with_efficiency_penalty(
            request,
            vehicle,
            route,
            candidates,
            efficiency_penalty=0.0,
        )

    def plan_robust(
        self,
        request: TripRequest,
        vehicle: Vehicle,
        route: Route,
        candidates: list[Candidate],
    ) -> list[Itinerary]:
        """Plan with the frozen robust energy-consumption penalty.

        This is a sensitivity run used to measure how many nominally feasible
        plans remain feasible when the vehicle consumes more energy than
        expected. It does not replace or modify the driver's normal plan.
        """

        efficiency_penalty = float(
            self.energy_config["efficiency_penalty_robust"]
        )

        if efficiency_penalty < 0:
            raise ValueError(
                "efficiency_penalty_robust must not be negative"
            )

        return self._plan_with_efficiency_penalty(
            request,
            vehicle,
            route,
            candidates,
            efficiency_penalty=efficiency_penalty,
        )

    def _plan_with_efficiency_penalty(
        self,
        request: TripRequest,
        vehicle: Vehicle,
        route: Route,
        candidates: list[Candidate],
        efficiency_penalty: float,
    ) -> list[Itinerary]:
        """Plan using one explicit energy-consumption penalty.

        Ordinary planning passes zero. The robust entry point passes the frozen
        sensitivity value without changing normal recommendations.
        """

        ordered_candidates = sorted(
            candidates, key=lambda candidate: candidate.along_route_km
        )
        stop_sequences = self._build_stop_sequences(
            request,
            vehicle,
            route,
            ordered_candidates,
            efficiency_penalty,
        )
        feasible_itineraries: list[Itinerary] = []

        for stop_sequence in stop_sequences:
            itinerary = simulate_itinerary(
                request,
                vehicle,
                route,
                stop_sequence,
                self.energy_config,
                efficiency_penalty,
            )

            if itinerary is not None:
                feasible_itineraries.append(itinerary)

        return feasible_itineraries

    def _build_stop_sequences(
        self,
        request: TripRequest,
        vehicle: Vehicle,
        route: Route,
        ordered_candidates: list[Candidate],
        efficiency_penalty: float,
    ) -> list[list[Candidate]]:
        """Build representative forward sequences of any feasible stop count.

        Labels are expanded one stop at a time. At each depth, only the
        lowest-detour label for the same final station and preference signature
        is retained. Keeping separate depths ensures that the search is not
        silently capped at the shortest path or at two stops.
        """

        stop_sequences: list[list[Candidate]] = [[]]
        stop_sequences.extend([candidate] for candidate in ordered_candidates)

        if len(ordered_candidates) < 2:
            return stop_sequences

        distance_scale = route_distance_scale(route)
        road_factor = self.energy_config["placeholder_detour"]["road_factor"]
        minutes_per_km = route.duration_min / route.distance_km
        origin_point = RoutePoint(
            label=request.origin.text,
            along_road_km=0.0,
            offroute_road_km=0.0,
            detour_min=0.0,
            candidate=None,
        )
        candidate_points = [
            candidate_route_point(candidate, distance_scale, road_factor)
            for candidate in ordered_candidates
        ]
        preference_keys_by_candidate = [
            self._satisfied_preference_keys(candidate, request)
            for candidate in ordered_candidates
        ]
        current_label_frontiers: dict[
            tuple[int, frozenset[str]],
            list[PlanningLabel],
        ] = {}

        for candidate_index, candidate_point in enumerate(candidate_points):
            first_leg_distance_km = leg_distance_km(
                origin_point,
                candidate_point,
            )
            first_leg_energy_kwh = energy_kwh(
                first_leg_distance_km,
                vehicle,
                efficiency_penalty,
            )
            arrival_soc_pct = request.start_soc_pct - kwh_to_soc_pct(
                first_leg_energy_kwh,
                vehicle,
            )

            if arrival_soc_pct < self.energy_config["reserve_soc_pct"]:
                continue

            preference_keys = preference_keys_by_candidate[candidate_index]
            initial_label = PlanningLabel(
                stop_indices=(candidate_index,),
                arrival_soc_pct=arrival_soc_pct,
                total_drive_min=leg_drive_min(
                    origin_point,
                    candidate_point,
                    minutes_per_km,
                ),
                total_charge_min=0.0,
                total_detour_min=candidate_point.detour_min,
                satisfied_preference_keys=preference_keys,
            )
            current_label_frontiers[(candidate_index, preference_keys)] = [
                initial_label
            ]

        # Each pass adds one stop. The loop ends when no label can move farther
        # along the route, even when unused candidates remain.
        while current_label_frontiers:
            next_label_frontiers: dict[
                tuple[int, frozenset[str]],
                list[PlanningLabel],
            ] = {}

            for label in (
                retained_label
                for label_frontier in current_label_frontiers.values()
                for retained_label in label_frontier
            ):
                last_stop_point = candidate_points[label.last_stop_index]

                for next_stop_index in range(
                    label.last_stop_index + 1,
                    len(ordered_candidates),
                ):
                    next_stop_point = candidate_points[next_stop_index]

                    if not self._station_can_reach(
                        last_stop_point,
                        next_stop_point,
                        vehicle,
                        efficiency_penalty,
                    ):
                        continue

                    last_station_candidate = last_stop_point.candidate

                    if last_station_candidate is None:
                        raise ValueError(
                            "a label stop must contain a charging candidate"
                        )

                    next_leg_distance_km = leg_distance_km(
                        last_stop_point,
                        next_stop_point,
                    )
                    departure_soc_pct_needed = required_departure_soc_pct(
                        next_leg_distance_km,
                        self.energy_config["reserve_soc_pct"],
                        vehicle,
                        self.energy_config,
                        efficiency_penalty,
                    )

                    if (
                        departure_soc_pct_needed
                        > self.energy_config["charge_target_max_soc_pct"]
                    ):
                        continue

                    departure_soc_pct = max(
                        label.arrival_soc_pct,
                        departure_soc_pct_needed,
                    )
                    compatible_charging_ports = compatible_ports(
                        vehicle,
                        last_station_candidate.station,
                        request.connectors_override,
                    )

                    if not compatible_charging_ports:
                        continue

                    selected_port = max(
                        compatible_charging_ports,
                        key=lambda port: port.power_kw or 0.0,
                    )
                    added_charge_min = estimate_charge_min(
                        vehicle,
                        selected_port.power_kw,
                        selected_port.level,
                        label.arrival_soc_pct,
                        departure_soc_pct,
                        self.energy_config["charging_model"],
                    )
                    next_leg_soc_drop_pct = kwh_to_soc_pct(
                        energy_kwh(
                            next_leg_distance_km,
                            vehicle,
                            efficiency_penalty,
                        ),
                        vehicle,
                    )
                    next_arrival_soc_pct = departure_soc_pct - next_leg_soc_drop_pct
                    preference_keys = (
                        label.satisfied_preference_keys
                        | preference_keys_by_candidate[next_stop_index]
                    )

                    expanded_label = PlanningLabel(
                        stop_indices=(label.stop_indices + (next_stop_index,)),
                        arrival_soc_pct=next_arrival_soc_pct,
                        total_drive_min=(
                            label.total_drive_min
                            + leg_drive_min(
                                last_stop_point,
                                next_stop_point,
                                minutes_per_km,
                            )
                        ),
                        total_charge_min=(label.total_charge_min + added_charge_min),
                        total_detour_min=(
                            label.total_detour_min + next_stop_point.detour_min
                        ),
                        satisfied_preference_keys=preference_keys,
                    )
                    frontier_key = (
                        next_stop_index,
                        preference_keys,
                    )
                    existing_frontier = next_label_frontiers.get(
                        frontier_key,
                        [],
                    )
                    next_label_frontiers[frontier_key] = retain_nondominated_label(
                        existing_frontier,
                        expanded_label,
                    )

            if not next_label_frontiers:
                break

            for label in (
                retained_label
                for label_frontier in next_label_frontiers.values()
                for retained_label in label_frontier
            ):
                stop_sequences.append(
                    [ordered_candidates[index] for index in label.stop_indices]
                )

            current_label_frontiers = next_label_frontiers

        return stop_sequences

    def _station_can_reach(
        self,
        start_point: RoutePoint,
        end_point: RoutePoint,
        vehicle: Vehicle,
        efficiency_penalty: float,
    ) -> bool:
        """Check whether a fully available charging stop can reach the next."""

        leg_energy_kwh = energy_kwh(
            leg_distance_km(start_point, end_point),
            vehicle,
            efficiency_penalty,
        )
        leg_soc_drop_pct = kwh_to_soc_pct(leg_energy_kwh, vehicle)
        required_departure_soc_pct = (
            self.energy_config["reserve_soc_pct"]
            + leg_soc_drop_pct
            + self.energy_config["charge_buffer_soc_pct"]
        )
        return (
            required_departure_soc_pct
            <= self.energy_config["charge_target_max_soc_pct"]
        )

    @staticmethod
    def _satisfied_preference_keys(
        candidate: Candidate,
        request: TripRequest,
    ) -> frozenset[str]:
        match_scores, _amenity_ids = satisfied_preferences(candidate, request)
        return frozenset(
            preference_key
            for preference_key, match_score in match_scores.items()
            if match_score > 0
        )
