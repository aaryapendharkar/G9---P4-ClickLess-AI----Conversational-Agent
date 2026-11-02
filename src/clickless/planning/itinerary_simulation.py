"""Final itinerary construction and state-of-charge simulation.

Forward search proposes ordered stop sequences. This module replays each full
sequence, rejects infeasible legs, selects charging ports, and constructs the
public itinerary contract.

Owner: Hriday.
"""

from __future__ import annotations

from typing import Any

from ..contracts import (
    Candidate,
    ChargingStop,
    Itinerary,
    Leg,
    Route,
    TripRequest,
    Vehicle,
)
from .charging_policy import departure_soc_target
from .charging_time import estimate_charge_min
from .energy import energy_kwh, kwh_to_soc_pct
from .filters import compatible_ports
from .route_measurement import ItineraryRouteMeasurement, measure_itinerary_route
from .route_points import (
    build_route_points,
    required_departure_soc_pct,
)


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


def simulate_itinerary(
    request: TripRequest,
    vehicle: Vehicle,
    route: Route,
    stops: list[Candidate],
    energy_config: dict[str, Any],
    efficiency_penalty: float,
    route_measurement: ItineraryRouteMeasurement | None = None,
) -> Itinerary | None:
    """Build one itinerary from measured legs, rejecting infeasible energy."""

    reserve_soc_pct = energy_config["reserve_soc_pct"]
    minimum_arrival_soc_pct = max(
        request.arrival_soc_pct_min or energy_config["default_arrival_soc_pct"],
        reserve_soc_pct,
    )
    route_points = build_route_points(
        request,
        route,
        stops,
        energy_config,
    )
    if route_measurement is None:
        # Direct callers and cache-mode tests retain the deterministic
        # geometric behavior. The pipeline injects its selected strategy.
        route_measurement = measure_itinerary_route(
            route,
            [candidate.station for candidate in stops],
            energy_config,
        )

    if len(route_measurement.legs) != len(route_points) - 1:
        raise ValueError(
            "route measurement must contain one leg between every itinerary point"
        )

    if len(route_measurement.stop_detour_min) != len(stops):
        raise ValueError(
            "route measurement must contain one detour for every charging stop"
        )

    current_soc_pct = request.start_soc_pct
    legs: list[Leg] = []
    charging_stops: list[ChargingStop] = []

    for leg_index in range(len(route_points) - 1):
        start_point = route_points[leg_index]
        end_point = route_points[leg_index + 1]
        measured_leg = route_measurement.legs[leg_index]
        distance_km = measured_leg.distance_km
        drive_min = measured_leg.duration_min
        required_energy_kwh = energy_kwh(
            distance_km,
            vehicle,
            efficiency_penalty,
        )
        arrival_soc_pct = current_soc_pct - kwh_to_soc_pct(
            required_energy_kwh,
            vehicle,
        )
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
                distance_km=distance_km,
                drive_min=drive_min,
                energy_kwh=required_energy_kwh,
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

        satisfied_preferences_by_key, amenity_ids = satisfied_preferences(
            charging_candidate,
            request,
        )
        satisfied_preference_keys = {
            preference_key
            for preference_key, match_score in satisfied_preferences_by_key.items()
            if match_score > 0
        }

        next_leg_distance_km = route_measurement.legs[
            leg_index + 1
        ].distance_km
        next_arrival_target_soc_pct = (
            minimum_arrival_soc_pct
            if leg_index + 2 == len(route_points) - 1
            else reserve_soc_pct
        )
        departure_soc_pct_needed = required_departure_soc_pct(
            next_leg_distance_km,
            next_arrival_target_soc_pct,
            vehicle,
            energy_config,
            efficiency_penalty,
        )

        if departure_soc_pct_needed > energy_config["charge_target_max_soc_pct"]:
            return None

        just_enough_departure_soc_pct = max(
            current_soc_pct,
            departure_soc_pct_needed,
        )
        departure_soc_pct = departure_soc_target(
            request,
            satisfied_preference_keys,
            just_enough_departure_soc_pct,
            energy_config,
        )
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
            energy_config["charging_model"],
        )
        charging_stops.append(
            ChargingStop(
                station_id=charging_candidate.station.station_id,
                station_name=charging_candidate.station.name,
                connector=selected_port.connector,
                port_power_kw=selected_port.power_kw,
                along_route_km=end_point.along_road_km,
                detour_min=route_measurement.stop_detour_min[leg_index],
                arrive_soc_pct=current_soc_pct,
                depart_soc_pct=departure_soc_pct,
                charge_min_est=charge_time_min,
                amenity_ids=amenity_ids,
                satisfied_preferences=satisfied_preferences_by_key,
            )
        )
        current_soc_pct = departure_soc_pct

    stop_identifier = "+".join(stop.station_id for stop in charging_stops) or "direct"
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
