"""Test OSRM-backed verifier measurements without network access."""

import pytest

from clickless.contracts import LatLon, Place, Route
from clickless.planning.route_measurement import OSRMRouteMeasurer


class _RecordingRouteProvider:
    """Return queued routes and record every requested waypoint sequence."""

    def __init__(self, routes: list[Route]) -> None:
        self.routes = routes
        self.requested_points: list[list[LatLon]] = []

    def route_through(self, route_points):
        self.requested_points.append(list(route_points))
        route_index = len(self.requested_points) - 1
        return self.routes[route_index]


def _route_for_points(
    base_route: Route,
    route_points: list[LatLon],
    distance_km: float,
    duration_min: float,
) -> Route:
    """Build one deterministic routed measurement for the fake provider."""

    return base_route.model_copy(
        update={
            "route_id": f"measured-route-{distance_km:g}-{duration_min:g}",
            "origin": route_points[0],
            "destination": route_points[-1],
            "via": route_points[1:-1],
            "geometry": route_points,
            "distance_km": distance_km,
            "duration_min": duration_min,
        }
    )


def _station_point(station) -> LatLon:
    """Convert an authoritative station record to a routing waypoint."""

    return LatLon(
        lat=station.lat,
        lon=station.lon,
    )


def _base_route(pipe_a) -> Route:
    """Load the deterministic Phoenix-to-Flagstaff fixture route."""

    return pipe_a.router.route(
        Place(text="Phoenix", lat=33.4484, lon=-112.0740),
        Place(text="Flagstaff", lat=35.1983, lon=-111.6513),
    )


def test_osrm_measurer_uses_routed_leg_distances_and_stop_detour(pipe_a):
    """One stop requires two leg routes and one through-station route."""

    base_route = _base_route(pipe_a)
    station = pipe_a.store.station("FIX-ST-004")
    station_point = _station_point(station)
    first_leg_points = [base_route.origin, station_point]
    second_leg_points = [station_point, base_route.destination]
    detour_points = [base_route.origin, station_point, base_route.destination]
    route_provider = _RecordingRouteProvider(
        [
            _route_for_points(base_route, first_leg_points, 145.0, 90.0),
            _route_for_points(base_route, second_leg_points, 96.0, 67.0),
            _route_for_points(
                base_route,
                detour_points,
                241.0,
                base_route.duration_min + 12.0,
            ),
        ]
    )

    measurement = OSRMRouteMeasurer(route_provider).measure(
        base_route,
        [station],
    )

    assert [leg.distance_km for leg in measurement.legs] == [145.0, 96.0]
    assert measurement.stop_detour_min == pytest.approx((12.0,))
    assert measurement.total_detour_min == pytest.approx(12.0)
    assert [point.along_road_km for point in measurement.points] == [
        0.0,
        145.0,
        241.0,
    ]
    assert route_provider.requested_points == [
        first_leg_points,
        second_leg_points,
        detour_points,
    ]


def test_osrm_measurer_preserves_multi_stop_order(pipe_a):
    """Every consecutive pair and individual stop is measured in trip order."""

    base_route = _base_route(pipe_a)
    first_station = pipe_a.store.station("FIX-ST-001")
    second_station = pipe_a.store.station("FIX-ST-004")
    first_station_point = _station_point(first_station)
    second_station_point = _station_point(second_station)
    requested_point_groups = [
        [base_route.origin, first_station_point],
        [first_station_point, second_station_point],
        [second_station_point, base_route.destination],
        [base_route.origin, first_station_point, base_route.destination],
        [base_route.origin, second_station_point, base_route.destination],
    ]
    route_provider = _RecordingRouteProvider(
        [
            _route_for_points(base_route, requested_point_groups[0], 50.0, 35.0),
            _route_for_points(base_route, requested_point_groups[1], 80.0, 50.0),
            _route_for_points(base_route, requested_point_groups[2], 100.0, 70.0),
            _route_for_points(
                base_route,
                requested_point_groups[3],
                235.0,
                base_route.duration_min + 5.0,
            ),
            _route_for_points(
                base_route,
                requested_point_groups[4],
                240.0,
                base_route.duration_min + 15.0,
            ),
        ]
    )

    measurement = OSRMRouteMeasurer(route_provider).measure(
        base_route,
        [first_station, second_station],
    )

    assert [leg.distance_km for leg in measurement.legs] == [50.0, 80.0, 100.0]
    assert measurement.stop_detour_min == pytest.approx((5.0, 15.0))
    assert [point.station_id for point in measurement.points] == [
        None,
        first_station.station_id,
        second_station.station_id,
        None,
    ]
    assert route_provider.requested_points == requested_point_groups


def test_osrm_measurer_clamps_negative_detour_to_zero(pipe_a):
    """Small routing fluctuations must never produce negative detour time."""

    base_route = _base_route(pipe_a)
    station = pipe_a.store.station("FIX-ST-004")
    station_point = _station_point(station)
    point_groups = [
        [base_route.origin, station_point],
        [station_point, base_route.destination],
        [base_route.origin, station_point, base_route.destination],
    ]
    route_provider = _RecordingRouteProvider(
        [
            _route_for_points(base_route, point_groups[0], 140.0, 85.0),
            _route_for_points(base_route, point_groups[1], 90.0, 55.0),
            _route_for_points(
                base_route,
                point_groups[2],
                230.0,
                base_route.duration_min - 5.0,
            ),
        ]
    )

    measurement = OSRMRouteMeasurer(route_provider).measure(
        base_route,
        [station],
    )

    assert measurement.stop_detour_min == (0.0,)
