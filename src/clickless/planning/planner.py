"""Itinerary construction. Owner: Hriday.

PLACEHOLDER (walking skeleton): only direct and single-stop itineraries, charging just enough for
the next leg. TODO(Hriday):
  * multi-stop search over corridor candidates ordered by route position (DP / label-setting,
    proposal ref [12]) -- the benchmark has sparse-charging and multi-stop categories
  * return several diverse feasible plans so the trade-off layer has real alternatives
  * charge-target policy: "just enough" vs "top up while eating" (comfort profile)
  * robust feasibility run with efficiency_penalty_robust
"""

from __future__ import annotations

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


def satisfied_preferences(
    candidate: Candidate, request: TripRequest
) -> tuple[dict[str, float], list[str]]:
    """Best match score per requested preference at this stop, applying the wheelchair rule:
    only amenities tagged wheelchair=yes count when accessibility is required ("unknown" never does).
    """
    scores: dict[str, float] = {}
    ids: set[str] = set()
    for pref in request.amenities:
        best = 0.0
        for na in candidate.amenities:
            if pref.wheelchair_required and na.amenity.wheelchair != "yes":
                continue
            s = na.match_scores.get(pref.key, 0.0)
            if s > 0:
                ids.add(na.amenity.amenity_id)
            best = max(best, s)
        scores[pref.key] = best
    return scores, sorted(ids)


class GreedyPlanner:
    def __init__(self, energy_cfg: dict[str, Any]) -> None:
        self.cfg = energy_cfg

    def plan(
        self,
        request: TripRequest,
        vehicle: Vehicle,
        route: Route,
        candidates: list[Candidate],
    ) -> list[Itinerary]:
        """Build feasible itineraries with zero, one, or two charging stops."""

        # A multi-stop itinerary must visit stations in forward route order.
        # Sorting here protects the planner even if a future retriever returns
        # candidates in a different order.
        ordered_candidates = sorted(
            candidates,
            key=lambda candidate: candidate.along_route_km,
        )

        stop_sequences: list[list[Candidate]] = [[]]

        # First consider every possible single-stop itinerary.
        stop_sequences.extend([candidate] for candidate in ordered_candidates)

        # Then consider every forward-moving pair of charging stations.
        # Starting the second slice after the first station prevents duplicate
        # pairs and prevents itineraries from travelling backward along the route.
        for first_stop_index, first_stop in enumerate(ordered_candidates):
            later_candidates = ordered_candidates[first_stop_index + 1 :]

            for second_stop in later_candidates:
                stop_sequences.append([first_stop, second_stop])

        feasible_itineraries: list[Itinerary] = []

        for stop_sequence in stop_sequences:
            itinerary = self._simulate(
                request,
                vehicle,
                route,
                stop_sequence,
            )

            if itinerary is not None:
                feasible_itineraries.append(itinerary)

        return feasible_itineraries

    # ------------------------------------------------------------------

    def _simulate(
        self,
        request: TripRequest,
        vehicle: Vehicle,
        route: Route,
        stops: list[Candidate],
    ) -> Itinerary | None:
        energy_config = self.cfg
        reserve = energy_config["reserve_soc_pct"]
        minimum_arrival_soc_pct = max(
            request.arrival_soc_pct_min or energy_config["default_arrival_soc_pct"],
            reserve,
        )
        road_factor = energy_config["placeholder_detour"]["road_factor"]
        route_distance_scale = route.distance_km / polyline_length_km(
            [(p.lat, p.lon) for p in route.geometry]
        )
        minutes_per_km = route.duration_min / route.distance_km

        # waypoints: (label, along_road_km, offroute_road_km, detour_min, candidate)
        route_points = [(request.origin.text, 0.0, 0.0, 0.0, None)]
        route_points += [
            (
                c.station.name,
                c.along_route_km * route_distance_scale,
                c.offroute_km * road_factor,
                c.detour_min_est or 0.0,
                c,
            )
            for c in stops
        ]
        route_points += [(request.destination.text, route.distance_km, 0.0, 0.0, None)]

        current_soc_pct = request.start_soc_pct
        legs: list[Leg] = []
        charging_stops: list[ChargingStop] = []

        for leg_index in range(len(route_points) - 1):
            start_point, end_point = (
                route_points[leg_index],
                route_points[leg_index + 1],
            )
            on_route = max(0.0, end_point[1] - start_point[1])
            leg_distance_km = on_route + start_point[2] + end_point[2]
            leg_drive_min = (
                on_route * minutes_per_km + (start_point[3] + end_point[3]) / 2
            )
            leg_energy_kwh = energy_kwh(leg_distance_km, vehicle)
            arrival_soc_pct = current_soc_pct - kwh_to_soc_pct(leg_energy_kwh, vehicle)
            is_last = leg_index == len(route_points) - 2

            if arrival_soc_pct < (minimum_arrival_soc_pct if is_last else reserve):
                return None

            legs.append(
                Leg(
                    start_label=start_point[0],
                    end_label=end_point[0],
                    distance_km=leg_distance_km,
                    drive_min=leg_drive_min,
                    energy_kwh=leg_energy_kwh,
                    soc_start_pct=current_soc_pct,
                    soc_end_pct=arrival_soc_pct,
                )
            )

            current_soc_pct = arrival_soc_pct

            if is_last:
                break

            # charge at stop b for the next leg
            c: Candidate = end_point[4]
            next_point = route_points[leg_index + 2]
            next_dist = (
                max(0.0, next_point[1] - end_point[1]) + end_point[2] + next_point[2]
            )
            next_target = (
                minimum_arrival_soc_pct
                if leg_index + 2 == len(route_points) - 1
                else reserve
            )
            next_drop = kwh_to_soc_pct(energy_kwh(next_dist, vehicle), vehicle)
            needed = next_target + next_drop + energy_config["charge_buffer_soc_pct"]

            if needed > energy_config["charge_target_max_soc_pct"]:
                return None

            depart = max(current_soc_pct, needed)
            ports = compatible_ports(vehicle, c.station, request.connectors_override)
            port = max(ports, key=lambda p: p.power_kw or 0.0)
            charge_min = estimate_charge_min(
                vehicle,
                port.power_kw,
                port.level,
                current_soc_pct,
                depart,
                energy_config["charging_model"],
            )
            satisfied_preferences_by_key, amenity_ids = satisfied_preferences(
                c, request
            )
            charging_stops.append(
                ChargingStop(
                    station_id=c.station.station_id,
                    station_name=c.station.name,
                    connector=port.connector,
                    port_power_kw=port.power_kw,
                    along_route_km=end_point[1],
                    detour_min=end_point[3],
                    arrive_soc_pct=current_soc_pct,
                    depart_soc_pct=depart,
                    charge_min_est=charge_min,
                    amenity_ids=amenity_ids,
                    satisfied_preferences=satisfied_preferences_by_key,
                )
            )
            current_soc_pct = depart

        stop_identifier = "+".join(s.station_id for s in charging_stops) or "direct"

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
