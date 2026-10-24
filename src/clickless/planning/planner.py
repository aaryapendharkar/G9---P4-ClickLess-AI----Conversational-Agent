"""Construct feasible EV itineraries. Owner: Hriday.

The planner performs a forward label-setting search over compatible corridor
stations. A label records one useful partial stop sequence. Labels only move
toward the destination, so the search cannot revisit a station or form a cycle.

The search supports any number of stops up to the number of corridor candidates;
it is not capped at two stops. To keep the search manageable, labels that reach
the same station with the same stop count and satisfied-preference signature are
reduced to the sequence with the smaller estimated detour.

TODO(Hriday):
  * include charging time and arrival state of charge in label dominance so the
    pruning rule can distinguish more multi-stop trade-offs at production scale
  * charge-target policy: "just enough" vs "top up while eating" (comfort profile)
  * robust feasibility run with efficiency_penalty_robust
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..common.geo import polyline_length_km
from ..contracts import (
    Candidate,
    ChargingStop,
    Itinerary,
    Leg,
    Route,
    TripRequest,
    Vehicle,
)
from .charging_time import estimate_charge_min
from .energy import energy_kwh, kwh_to_soc_pct
from .filters import compatible_ports


@dataclass(frozen=True)
class RoutePoint:
    """One ordered point used while simulating an itinerary.

    ``candidate`` is absent for the trip origin and destination. Distances are
    road-distance estimates, not the straight-line values stored by retrieval.
    """

    label: str
    along_road_km: float
    offroute_road_km: float
    detour_min: float
    candidate: Candidate | None


@dataclass(frozen=True)
class PlanningLabel:
    """A partial forward path retained by the multi-stop search."""

    stop_indices: tuple[int, ...]
    total_detour_min: float
    satisfied_preference_keys: frozenset[str]

    @property
    def last_stop_index(self) -> int:
        return self.stop_indices[-1]


def satisfied_preferences(
    candidate: Candidate,
    request: TripRequest,
) -> tuple[dict[str, float], list[str]]:
    """Return the best match per requested preference at one charging stop.

    If wheelchair access is required, only amenities explicitly marked
    ``wheelchair=yes`` count. An unknown value is never treated as accessible.
    """

    match_score_by_preference: dict[str, float] = {}
    matched_amenity_ids: set[str] = set()

    for preference in request.amenities:
        best_match_score = 0.0

        for nearby_amenity in candidate.amenities:
            if (
                preference.wheelchair_required
                and nearby_amenity.amenity.wheelchair != "yes"
            ):
                continue

            match_score = nearby_amenity.match_scores.get(preference.key, 0.0)

            if match_score > 0:
                matched_amenity_ids.add(nearby_amenity.amenity.amenity_id)

            best_match_score = max(best_match_score, match_score)

        match_score_by_preference[preference.key] = best_match_score

    return match_score_by_preference, sorted(matched_amenity_ids)


class ItineraryPlanner:
    """Generate and simulate feasible direct and multi-stop itineraries."""

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

        ordered_candidates = sorted(
            candidates, key=lambda candidate: candidate.along_route_km
        )
        stop_sequences = self._build_stop_sequences(
            request,
            vehicle,
            route,
            ordered_candidates,
        )
        feasible_itineraries: list[Itinerary] = []

        for stop_sequence in stop_sequences:
            itinerary = self._simulate(request, vehicle, route, stop_sequence)

            if itinerary is not None:
                feasible_itineraries.append(itinerary)

        return feasible_itineraries

    def _build_stop_sequences(
        self,
        request: TripRequest,
        vehicle: Vehicle,
        route: Route,
        ordered_candidates: list[Candidate],
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

        route_distance_scale = self._route_distance_scale(route)
        road_factor = self.energy_config["placeholder_detour"]["road_factor"]
        origin_point = RoutePoint(
            label=request.origin.text,
            along_road_km=0.0,
            offroute_road_km=0.0,
            detour_min=0.0,
            candidate=None,
        )
        candidate_points = [
            self._candidate_route_point(candidate, route_distance_scale, road_factor)
            for candidate in ordered_candidates
        ]
        preference_keys_by_candidate = [
            self._satisfied_preference_keys(candidate, request)
            for candidate in ordered_candidates
        ]
        current_labels: dict[tuple[int, frozenset[str]], PlanningLabel] = {}

        for candidate_index, candidate_point in enumerate(candidate_points):
            if not self._origin_can_reach(
                origin_point,
                candidate_point,
                request.start_soc_pct,
                vehicle,
            ):
                continue

            preference_keys = preference_keys_by_candidate[candidate_index]
            current_labels[(candidate_index, preference_keys)] = PlanningLabel(
                stop_indices=(candidate_index,),
                total_detour_min=candidate_point.detour_min,
                satisfied_preference_keys=preference_keys,
            )

        # Each pass adds one stop. The loop ends when no label can move farther
        # along the route, even when unused candidates remain.
        while current_labels:
            next_labels: dict[tuple[int, frozenset[str]], PlanningLabel] = {}

            for label in current_labels.values():
                last_stop_point = candidate_points[label.last_stop_index]

                for next_stop_index in range(
                    label.last_stop_index + 1,
                    len(ordered_candidates),
                ):
                    next_stop_point = candidate_points[next_stop_index]

                    if not self._station_can_reach(
                        last_stop_point, next_stop_point, vehicle
                    ):
                        continue

                    preference_keys = (
                        label.satisfied_preference_keys
                        | preference_keys_by_candidate[next_stop_index]
                    )
                    expanded_label = PlanningLabel(
                        stop_indices=label.stop_indices + (next_stop_index,),
                        total_detour_min=(
                            label.total_detour_min + next_stop_point.detour_min
                        ),
                        satisfied_preference_keys=preference_keys,
                    )
                    label_key = (next_stop_index, preference_keys)
                    existing_label = next_labels.get(label_key)

                    if (
                        existing_label is None
                        or expanded_label.total_detour_min
                        < existing_label.total_detour_min
                    ):
                        next_labels[label_key] = expanded_label

            if not next_labels:
                break

            for label in next_labels.values():
                stop_sequences.append(
                    [ordered_candidates[index] for index in label.stop_indices]
                )

            current_labels = next_labels

        return stop_sequences

    def _simulate(
        self,
        request: TripRequest,
        vehicle: Vehicle,
        route: Route,
        stops: list[Candidate],
    ) -> Itinerary | None:
        """Simulate one ordered stop sequence, rejecting infeasible legs."""

        reserve_soc_pct = self.energy_config["reserve_soc_pct"]
        minimum_arrival_soc_pct = max(
            request.arrival_soc_pct_min
            or self.energy_config["default_arrival_soc_pct"],
            reserve_soc_pct,
        )
        route_points = self._route_points(request, route, stops)
        minutes_per_km = route.duration_min / route.distance_km
        current_soc_pct = request.start_soc_pct
        legs: list[Leg] = []
        charging_stops: list[ChargingStop] = []

        for leg_index in range(len(route_points) - 1):
            start_point = route_points[leg_index]
            end_point = route_points[leg_index + 1]
            leg_distance_km = self._leg_distance_km(start_point, end_point)
            on_route_distance_km = max(
                0.0,
                end_point.along_road_km - start_point.along_road_km,
            )
            leg_drive_min = (
                on_route_distance_km * minutes_per_km
                + (start_point.detour_min + end_point.detour_min) / 2
            )
            leg_energy_kwh = energy_kwh(leg_distance_km, vehicle)
            arrival_soc_pct = current_soc_pct - kwh_to_soc_pct(leg_energy_kwh, vehicle)
            is_destination_leg = leg_index == len(route_points) - 2
            required_arrival_soc_pct = (
                minimum_arrival_soc_pct if is_destination_leg else reserve_soc_pct
            )

            if arrival_soc_pct < required_arrival_soc_pct:
                return None

            legs.append(
                Leg(
                    start_label=start_point.label,
                    end_label=end_point.label,
                    distance_km=leg_distance_km,
                    drive_min=leg_drive_min,
                    energy_kwh=leg_energy_kwh,
                    soc_start_pct=current_soc_pct,
                    soc_end_pct=arrival_soc_pct,
                )
            )
            current_soc_pct = arrival_soc_pct

            if is_destination_leg:
                break

            charging_candidate = end_point.candidate

            if charging_candidate is None:
                raise ValueError("an intermediate route point must contain a candidate")

            next_point = route_points[leg_index + 2]
            next_leg_distance_km = self._leg_distance_km(end_point, next_point)
            next_arrival_target_soc_pct = (
                minimum_arrival_soc_pct
                if leg_index + 2 == len(route_points) - 1
                else reserve_soc_pct
            )
            next_leg_soc_drop_pct = kwh_to_soc_pct(
                energy_kwh(next_leg_distance_km, vehicle),
                vehicle,
            )
            required_departure_soc_pct = (
                next_arrival_target_soc_pct
                + next_leg_soc_drop_pct
                + self.energy_config["charge_buffer_soc_pct"]
            )

            if (
                required_departure_soc_pct
                > self.energy_config["charge_target_max_soc_pct"]
            ):
                return None

            departure_soc_pct = max(current_soc_pct, required_departure_soc_pct)
            compatible_charging_ports = compatible_ports(
                vehicle,
                charging_candidate.station,
                request.connectors_override,
            )
            selected_port = max(
                compatible_charging_ports,
                key=lambda port: port.power_kw or 0.0,
            )
            charge_time_min = estimate_charge_min(
                vehicle,
                selected_port.power_kw,
                selected_port.level,
                current_soc_pct,
                departure_soc_pct,
                self.energy_config["charging_model"],
            )
            satisfied_preferences_by_key, amenity_ids = satisfied_preferences(
                charging_candidate,
                request,
            )
            charging_stops.append(
                ChargingStop(
                    station_id=charging_candidate.station.station_id,
                    station_name=charging_candidate.station.name,
                    connector=selected_port.connector,
                    port_power_kw=selected_port.power_kw,
                    along_route_km=end_point.along_road_km,
                    detour_min=end_point.detour_min,
                    arrive_soc_pct=current_soc_pct,
                    depart_soc_pct=departure_soc_pct,
                    charge_min_est=charge_time_min,
                    amenity_ids=amenity_ids,
                    satisfied_preferences=satisfied_preferences_by_key,
                )
            )
            current_soc_pct = departure_soc_pct

        stop_identifier = (
            "+".join(stop.station_id for stop in charging_stops) or "direct"
        )

        return Itinerary(
            itinerary_id=f"{request.request_id}:{stop_identifier}",
            request_id=request.request_id,
            vehicle_id=vehicle.vehicle_id,
            stops=charging_stops,
            legs=legs,
            total_drive_min=sum(leg.drive_min for leg in legs),
            total_charge_min=sum(
                charging_stop.charge_min_est for charging_stop in charging_stops
            ),
            total_detour_min=sum(
                charging_stop.detour_min for charging_stop in charging_stops
            ),
            arrival_soc_pct=current_soc_pct,
        )

    def _route_points(
        self,
        request: TripRequest,
        route: Route,
        stops: list[Candidate],
    ) -> list[RoutePoint]:
        """Convert origin, stations, and destination into simulator points."""

        route_distance_scale = self._route_distance_scale(route)
        road_factor = self.energy_config["placeholder_detour"]["road_factor"]
        route_points = [
            RoutePoint(
                label=request.origin.text,
                along_road_km=0.0,
                offroute_road_km=0.0,
                detour_min=0.0,
                candidate=None,
            )
        ]
        route_points.extend(
            self._candidate_route_point(candidate, route_distance_scale, road_factor)
            for candidate in stops
        )
        route_points.append(
            RoutePoint(
                label=request.destination.text,
                along_road_km=route.distance_km,
                offroute_road_km=0.0,
                detour_min=0.0,
                candidate=None,
            )
        )
        return route_points

    @staticmethod
    def _candidate_route_point(
        candidate: Candidate,
        route_distance_scale: float,
        road_factor: float,
    ) -> RoutePoint:
        return RoutePoint(
            label=candidate.station.name,
            along_road_km=candidate.along_route_km * route_distance_scale,
            offroute_road_km=candidate.offroute_km * road_factor,
            detour_min=candidate.detour_min_est or 0.0,
            candidate=candidate,
        )

    @staticmethod
    def _route_distance_scale(route: Route) -> float:
        geometry_distance_km = polyline_length_km(
            [(point.lat, point.lon) for point in route.geometry]
        )
        return route.distance_km / geometry_distance_km

    @staticmethod
    def _leg_distance_km(start_point: RoutePoint, end_point: RoutePoint) -> float:
        on_route_distance_km = max(
            0.0,
            end_point.along_road_km - start_point.along_road_km,
        )
        return (
            on_route_distance_km
            + start_point.offroute_road_km
            + end_point.offroute_road_km
        )

    def _origin_can_reach(
        self,
        origin_point: RoutePoint,
        candidate_point: RoutePoint,
        starting_soc_pct: float,
        vehicle: Vehicle,
    ) -> bool:
        leg_energy_kwh = energy_kwh(
            self._leg_distance_km(origin_point, candidate_point),
            vehicle,
        )
        arrival_soc_pct = starting_soc_pct - kwh_to_soc_pct(leg_energy_kwh, vehicle)
        return arrival_soc_pct >= self.energy_config["reserve_soc_pct"]

    def _station_can_reach(
        self,
        start_point: RoutePoint,
        end_point: RoutePoint,
        vehicle: Vehicle,
    ) -> bool:
        """Check whether a fully available charging stop can reach the next."""

        leg_energy_kwh = energy_kwh(
            self._leg_distance_km(start_point, end_point),
            vehicle,
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
