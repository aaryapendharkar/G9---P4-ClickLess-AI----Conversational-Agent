"""Independent route measurements used by itinerary verification.

Configuration A recomputes estimates from cached geometry and authoritative
station records. Runtime OSRM mode measures consecutive legs and stop detours
through the cache-first waypoint router. A policy wrapper may use geometry only
for explicitly temporary OSRM failures and records that source change for the
caller. No strategy trusts values copied into a planner-produced itinerary.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from ..common.geo import nearest_on_polyline, polyline_length_km
from ..contracts import LatLon, Route, Station
from .routing import OSRMTemporaryError


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
    """Distance and driving time between consecutive itinerary points."""

    start_label: str
    end_label: str
    distance_km: float
    duration_min: float


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


class ItineraryRouteMeasurer(Protocol):
    """Strategy used by the verifier to measure an itinerary's route.

    The verifier depends on this small behavior instead of a particular
    geometric or OSRM implementation. That keeps verification control flow
    independent while allowing the configured routing source to change.
    """

    def measure(
        self,
        route: Route,
        stations: Sequence[Station],
    ) -> ItineraryRouteMeasurement: ...


class WaypointRouteProvider(Protocol):
    """Routing source that can measure an ordered list of road points."""

    def route_through(
        self,
        route_points: Sequence[LatLon],
    ) -> Route: ...


class TemporaryFailureFallbackMeasurer:
    """Use geometry only when OSRM fails for a temporary service reason.

    The strict OSRM client remains responsible for detecting and reporting
    routing failures. This policy decides whether one of those failures may
    safely use the documented deterministic estimate.
    """

    def __init__(
        self,
        primary_measurer: ItineraryRouteMeasurer,
        fallback_measurer: ItineraryRouteMeasurer,
    ) -> None:
        self.primary_measurer = primary_measurer
        self.fallback_measurer = fallback_measurer
        self.selected_source: str | None = None
        self.warning: str | None = None

    def measure(
        self,
        route: Route,
        stations: Sequence[Station],
    ) -> ItineraryRouteMeasurement:
        """Return OSRM measurements or an explicitly labelled estimate."""

        # State belongs to one measurement attempt. Clear the previous result
        # so a recovered OSRM service does not retain an outdated warning.
        self.selected_source = None
        self.warning = None

        try:
            measurement = self.primary_measurer.measure(
                route,
                stations,
            )
        except OSRMTemporaryError as error:
            # Only OSRMTemporaryError reaches this branch. NoRoute, malformed
            # responses, invalid input and configuration errors remain visible.
            fallback_measurement = self.fallback_measurer.measure(
                route,
                stations,
            )
            self.selected_source = "geometry_fallback"
            self.warning = (
                f"OSRM measurement unavailable ({error}); "
                "using a deterministic geometric estimate."
            )
            return fallback_measurement

        self.selected_source = "osrm"
        return measurement


class GeometricRouteMeasurer:
    """Deterministic Configuration A measurement strategy."""

    def __init__(self, energy_config: dict[str, Any]) -> None:
        self.energy_config = energy_config

    def measure(
        self,
        route: Route,
        stations: Sequence[Station],
    ) -> ItineraryRouteMeasurement:
        """Measure from cached geometry and authoritative station records."""

        return measure_itinerary_route(
            route,
            stations,
            self.energy_config,
        )


class OSRMRouteMeasurer:
    """Measure itinerary legs and stop detours using routed road distances.

    The supplied route provider is expected to be cache-first. In normal
    pipeline use this will be ``CachingOSRMRouter``, so repeated verification
    can reuse saved OSRM responses without making another network request.
    """

    def __init__(
        self,
        route_provider: WaypointRouteProvider,
    ) -> None:
        self.route_provider = route_provider

    def measure(
        self,
        route: Route,
        stations: Sequence[Station],
    ) -> ItineraryRouteMeasurement:
        """Measure every declared leg and each station's individual detour."""

        station_route_points = [
            LatLon(
                lat=station.lat,
                lon=station.lon,
            )
            for station in stations
        ]
        itinerary_route_points = [
            route.origin,
            *station_route_points,
            route.destination,
        ]

        # Request each consecutive leg separately because the current Route
        # contract stores only a route's total distance, not OSRM's per-leg
        # breakdown. Each request still passes through the route cache.
        measured_leg_routes = [
            self.route_provider.route_through(
                [
                    itinerary_route_points[point_index],
                    itinerary_route_points[point_index + 1],
                ]
            )
            for point_index in range(len(itinerary_route_points) - 1)
        ]

        point_labels = [
            "route origin",
            *(station.name for station in stations),
            "route destination",
        ]
        measured_legs = tuple(
            MeasuredLeg(
                start_label=point_labels[leg_index],
                end_label=point_labels[leg_index + 1],
                distance_km=measured_route.distance_km,
                duration_min=measured_route.duration_min,
            )
            for leg_index, measured_route in enumerate(measured_leg_routes)
        )

        # Planner detours are defined per station: compare the direct route with
        # a route through that one station. Use the same definition here so the
        # verifier compares independently sourced but semantically equal values.
        measured_stop_detours = tuple(
            self._measure_stop_detour(
                route,
                station_route_point,
            )
            for station_route_point in station_route_points
        )

        # The verifier currently consumes leg distances and stop detours.
        # Points are retained for a complete, inspectable measurement record.
        cumulative_distance_km = 0.0
        measured_points: list[MeasuredRoutePoint] = [
            MeasuredRoutePoint(
                label="route origin",
                station_id=None,
                along_road_km=0.0,
                offroute_road_km=0.0,
                detour_min=0.0,
            )
        ]

        for station_index, station in enumerate(stations):
            cumulative_distance_km += measured_legs[station_index].distance_km
            measured_points.append(
                MeasuredRoutePoint(
                    label=station.name,
                    station_id=station.station_id,
                    along_road_km=cumulative_distance_km,
                    offroute_road_km=0.0,
                    detour_min=measured_stop_detours[station_index],
                )
            )

        cumulative_distance_km += measured_legs[-1].distance_km
        measured_points.append(
            MeasuredRoutePoint(
                label="route destination",
                station_id=None,
                along_road_km=cumulative_distance_km,
                offroute_road_km=0.0,
                detour_min=0.0,
            )
        )

        return ItineraryRouteMeasurement(
            points=tuple(measured_points),
            legs=measured_legs,
            stop_detour_min=measured_stop_detours,
        )

    def _measure_stop_detour(
        self,
        direct_route: Route,
        station_route_point: LatLon,
    ) -> float:
        """Return added minutes for routing through one charging station."""

        route_via_station = self.route_provider.route_through(
            [
                direct_route.origin,
                station_route_point,
                direct_route.destination,
            ]
        )

        return max(
            0.0,
            route_via_station.duration_min - direct_route.duration_min,
        )


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
    minutes_per_km = route.duration_min / route.distance_km
    measured_legs = tuple(
        _measure_leg(
            measured_points[index],
            measured_points[index + 1],
            minutes_per_km,
        )
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
    minutes_per_km: float,
) -> MeasuredLeg:
    """Measure travel from one ordered route point to the next."""

    on_route_distance_km = end_point.along_road_km - start_point.along_road_km

    if on_route_distance_km < 0:
        raise ValueError(
            f"{end_point.label!r} appears before " f"{start_point.label!r} on the route"
        )

    # If a leg starts or ends at a station, include the appropriate connection
    # between that station and the main route.
    total_leg_distance_km = (
        on_route_distance_km + start_point.offroute_road_km + end_point.offroute_road_km
    )
    estimated_duration_min = (
        on_route_distance_km * minutes_per_km
        + (start_point.detour_min + end_point.detour_min) / 2
    )

    return MeasuredLeg(
        start_label=start_point.label,
        end_label=end_point.label,
        distance_km=total_leg_distance_km,
        duration_min=estimated_duration_min,
    )
