"""Shared route-point and leg calculations for itinerary planning.

Both forward search and final simulation use these functions. Keeping the
formulas in one module prevents the search from accepting a sequence that the
simulator measures differently.

Owner: Hriday.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..common.geo import polyline_length_km
from ..contracts import Candidate, Route, TripRequest, Vehicle
from .energy import energy_kwh, kwh_to_soc_pct


@dataclass(frozen=True)
class RoutePoint:
    """One ordered point used while searching or simulating an itinerary.

    ``candidate`` is absent for the trip origin and destination. Distances are
    road-distance estimates, not the straight-line values stored by retrieval.
    """

    label: str
    along_road_km: float
    offroute_road_km: float
    detour_min: float
    candidate: Candidate | None


def build_route_points(
    request: TripRequest,
    route: Route,
    stops: list[Candidate],
    energy_config: dict[str, Any],
) -> list[RoutePoint]:
    """Convert the origin, ordered stations, and destination to route points."""

    distance_scale = route_distance_scale(route)
    road_factor = energy_config["placeholder_detour"]["road_factor"]
    points = [
        RoutePoint(
            label=request.origin.text,
            along_road_km=0.0,
            offroute_road_km=0.0,
            detour_min=0.0,
            candidate=None,
        )
    ]
    points.extend(
        candidate_route_point(candidate, distance_scale, road_factor)
        for candidate in stops
    )
    points.append(
        RoutePoint(
            label=request.destination.text,
            along_road_km=route.distance_km,
            offroute_road_km=0.0,
            detour_min=0.0,
            candidate=None,
        )
    )
    return points


def candidate_route_point(
    candidate: Candidate,
    distance_scale: float,
    road_factor: float,
) -> RoutePoint:
    """Convert one retrieved charging candidate to planner road coordinates."""

    return RoutePoint(
        label=candidate.station.name,
        along_road_km=candidate.along_route_km * distance_scale,
        offroute_road_km=candidate.offroute_km * road_factor,
        detour_min=candidate.detour_min_est or 0.0,
        candidate=candidate,
    )


def route_distance_scale(route: Route) -> float:
    """Scale positions along route geometry to the reported road distance."""

    geometry_distance_km = polyline_length_km(
        [(point.lat, point.lon) for point in route.geometry]
    )
    return route.distance_km / geometry_distance_km


def leg_distance_km(
    start_point: RoutePoint,
    end_point: RoutePoint,
) -> float:
    """Return road distance including off-route station connections."""

    on_route_distance_km = max(
        0.0,
        end_point.along_road_km - start_point.along_road_km,
    )
    return (
        on_route_distance_km
        + start_point.offroute_road_km
        + end_point.offroute_road_km
    )


def leg_drive_min(
    start_point: RoutePoint,
    end_point: RoutePoint,
    minutes_per_km: float,
) -> float:
    """Estimate on-route time plus each endpoint's share of its detour."""

    on_route_distance_km = max(
        0.0,
        end_point.along_road_km - start_point.along_road_km,
    )
    return (
        on_route_distance_km * minutes_per_km
        + (start_point.detour_min + end_point.detour_min) / 2
    )


def required_departure_soc_pct(
    leg_distance_km_value: float,
    required_arrival_soc_pct: float,
    vehicle: Vehicle,
    energy_config: dict[str, Any],
    efficiency_penalty: float,
) -> float:
    """Return the departure charge needed for one subsequent leg."""

    leg_energy_kwh = energy_kwh(
        leg_distance_km_value,
        vehicle,
        efficiency_penalty,
    )
    leg_soc_drop_pct = kwh_to_soc_pct(
        leg_energy_kwh,
        vehicle,
    )
    return (
        required_arrival_soc_pct
        + leg_soc_drop_pct
        + energy_config["charge_buffer_soc_pct"]
    )
