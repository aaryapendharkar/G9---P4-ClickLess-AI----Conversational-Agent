"""Independent route measurements used by itinerary verification.

Configuration A uses a cached base route and synthetic station records. This
module recomputes station positions, leg distances, and detours from those
authoritative inputs instead of trusting values copied into an itinerary.

The calculations are still estimates. Live OSRM waypoint routing will replace
this approximation in a later milestone.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from ..common.geo import nearest_on_polyline, polyline_length_km
from ..contracts import Route, Station


@dataclass(frozen=True)
class MeasuredRoutePoint:
    """One independently measured point along an itinerary.

    The origin and destination have no station ID and no off-route distance.
    Charging stations are projected onto the cached route geometry.
    """

    label: str
    station_id: str | None
    along_road_km: float
    offroute_road_km: float
    detour_min: float


@dataclass(frozen=True)
class MeasuredLeg:
    """Expected distance between two consecutive itinerary points."""

    start_label: str
    end_label: str
    distance_km: float


@dataclass(frozen=True)
class ItineraryRouteMeasurement:
    """All independently calculated route values for one itinerary."""

    points: tuple[MeasuredRoutePoint, ...]
    legs: tuple[MeasuredLeg, ...]
    stop_detour_min: tuple[float, ...]

    @property
    def total_detour_min(self) -> float:
        """Return the total estimated detour across all charging stops."""

        return sum(self.stop_detour_min)


def measure_itinerary_route(
    route: Route,
    stations: Sequence[Station],
    energy_config: dict[str, Any],
) -> ItineraryRouteMeasurement:
    """Measure a route through the supplied charging stations.

    Station order comes from the itinerary. Station positions and distances
    come from authoritative station coordinates and cached route geometry.
    """

    # The polyline is the shape of the cached route. Its geometric length and
    # the route's reported road length differ, so we calculate a scale below.
    geometry = [(point.lat, point.lon) for point in route.geometry]
    geometry_distance_km = polyline_length_km(geometry)

    if geometry_distance_km <= 0:
        raise ValueError("route geometry must have a positive length")

    # The cached route stores road distance, while the geometry length is a
    # straight-line approximation. This scale converts positions along the
    # geometry into approximate positions along the road.
    road_distance_scale = route.distance_km / geometry_distance_km

    detour_config = energy_config["placeholder_detour"]
    road_factor = detour_config["road_factor"]
    fixed_detour_min = detour_config["fixed_min"]
    offroute_speed_kmh = detour_config["offroute_speed_kmh"]

    # Build one ordered list: origin, every charging station, destination.
    # Keeping all three point types together makes consecutive leg creation
    # straightforward and avoids special cases for one-stop or multi-stop trips.
    measured_points: list[MeasuredRoutePoint] = [
        MeasuredRoutePoint(
            label="route origin",
            station_id=None,
            along_road_km=0.0,
            offroute_road_km=0.0,
            detour_min=0.0,
        )
    ]

    for station in stations:
        (
            straight_line_offroute_km,
            _nearest_segment_index,
            along_geometry_km,
        ) = nearest_on_polyline(
            station.lat,
            station.lon,
            geometry,
        )

        along_road_km = along_geometry_km * road_distance_scale
        offroute_road_km = straight_line_offroute_km * road_factor

        # A complete stop detour includes leaving the route and returning to it.
        round_trip_offroute_km = 2 * offroute_road_km
        detour_min = (
            fixed_detour_min + 60.0 * round_trip_offroute_km / offroute_speed_kmh
        )

        measured_points.append(
            MeasuredRoutePoint(
                label=station.name,
                station_id=station.station_id,
                along_road_km=along_road_km,
                offroute_road_km=offroute_road_km,
                detour_min=detour_min,
            )
        )

    measured_points.append(
        MeasuredRoutePoint(
            label="route destination",
            station_id=None,
            along_road_km=route.distance_km,
            offroute_road_km=0.0,
            detour_min=0.0,
        )
    )

    # Adjacent points define legs. With N charging stops this produces N + 1
    # legs, matching the structure checked by the verifier.
    measured_legs = tuple(
        _measure_leg(measured_points[index], measured_points[index + 1])
        for index in range(len(measured_points) - 1)
    )

    return ItineraryRouteMeasurement(
        points=tuple(measured_points),
        legs=measured_legs,
        stop_detour_min=tuple(point.detour_min for point in measured_points[1:-1]),
    )


def _measure_leg(
    start_point: MeasuredRoutePoint,
    end_point: MeasuredRoutePoint,
) -> MeasuredLeg:
    """Measure travel from one ordered route point to the next."""

    on_route_distance_km = end_point.along_road_km - start_point.along_road_km

    if on_route_distance_km < 0:
        raise ValueError(
            f"{end_point.label!r} appears before "
            f"{start_point.label!r} on the route"
        )

    # If a leg starts or ends at a station, include the appropriate connection
    # between that station and the main route.
    total_leg_distance_km = (
        on_route_distance_km
        + start_point.offroute_road_km
        + end_point.offroute_road_km
    )

    return MeasuredLeg(
        start_label=start_point.label,
        end_label=end_point.label,
        distance_km=total_leg_distance_km,
    )
