"""Test when routing uses cached data instead of live OSRM.

The live OSRM client is replaced with a mock, so these tests use no network.
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

from clickless.contracts import (
    Candidate,
    DataStatus,
    LatLon,
    Place,
    Provenance,
    Route,
)
from clickless.planning.route_cache import OSRMRouteCache
from clickless.planning.routing import CachingOSRMRouter, OSRMRouter


def _phoenix_and_flagstaff() -> tuple[Place, Place]:
    """Return named places accepted by the public Router interface."""

    phoenix = Place(
        text="Phoenix",
        lat=33.4484,
        lon=-112.0740,
    )
    flagstaff = Place(
        text="Flagstaff",
        lat=35.1983,
        lon=-111.6513,
    )

    return phoenix, flagstaff


def _direct_runtime_route() -> Route:
    """Return a direct route resembling a live OSRM result."""

    return Route(
        route_id="osrm-direct-test-route",
        origin=LatLon(
            lat=33.4484,
            lon=-112.0740,
        ),
        destination=LatLon(
            lat=35.1983,
            lon=-111.6513,
        ),
        geometry=[
            LatLon(
                lat=33.4484,
                lon=-112.0740,
            ),
            LatLon(
                lat=35.1983,
                lon=-111.6513,
            ),
        ],
        distance_km=232.0,
        duration_min=145.0,
        provenance=Provenance(
            source="OSRM",
            source_url="https://router.project-osrm.org",
            retrieved_at=datetime(
                2026,
                10,
                30,
                tzinfo=timezone.utc,
            ),
            status=DataStatus.RUNTIME,
        ),
    )


def test_cache_miss_fetches_and_saves_live_route(tmp_path: Path):
    """The first request should use OSRM and populate the local cache."""

    origin, destination = _phoenix_and_flagstaff()
    live_route = _direct_runtime_route()

    live_osrm_router = Mock(spec=OSRMRouter)
    live_osrm_router.route_through.return_value = live_route

    route_cache = OSRMRouteCache(tmp_path / "osrm")
    router = CachingOSRMRouter(
        live_router=live_osrm_router,
        route_cache=route_cache,
    )

    returned_route = router.route(
        origin,
        destination,
    )

    requested_route_points = [
        live_route.origin,
        live_route.destination,
    ]
    cached_route = route_cache.load(requested_route_points)

    assert returned_route == live_route
    assert returned_route.provenance.status == DataStatus.RUNTIME
    assert cached_route is not None
    assert cached_route.provenance.status == DataStatus.CACHED

    live_osrm_router.route_through.assert_called_once_with(requested_route_points)


def test_cache_hit_does_not_call_live_router(tmp_path: Path):
    """A repeated request should reuse disk data without contacting OSRM."""

    origin, destination = _phoenix_and_flagstaff()
    live_route = _direct_runtime_route()
    route_points = [
        live_route.origin,
        live_route.destination,
    ]

    route_cache = OSRMRouteCache(tmp_path / "osrm")
    route_cache.save(
        route_points,
        live_route,
    )

    live_osrm_router = Mock(spec=OSRMRouter)
    router = CachingOSRMRouter(
        live_router=live_osrm_router,
        route_cache=route_cache,
    )

    returned_route = router.route(
        origin,
        destination,
    )

    assert returned_route.route_id == live_route.route_id
    assert returned_route.provenance.status == DataStatus.CACHED
    live_osrm_router.route_through.assert_not_called()


def test_detour_reuses_cached_waypoint_route(
    tmp_path: Path,
    store,
):
    """A cached station route should provide detour time without live OSRM."""

    direct_route = _direct_runtime_route()
    camp_verde_station = store.station("FIX-ST-004")
    station_coordinates = LatLon(
        lat=camp_verde_station.lat,
        lon=camp_verde_station.lon,
    )

    route_via_station = Route(
        route_id="osrm-camp-verde-test-route",
        origin=direct_route.origin,
        destination=direct_route.destination,
        via=[station_coordinates],
        geometry=[
            direct_route.origin,
            station_coordinates,
            direct_route.destination,
        ],
        distance_km=241.0,
        duration_min=157.0,
        provenance=direct_route.provenance,
    )

    route_points = [
        direct_route.origin,
        station_coordinates,
        direct_route.destination,
    ]

    route_cache = OSRMRouteCache(tmp_path / "osrm")
    route_cache.save(
        route_points,
        route_via_station,
    )

    live_osrm_router = Mock(spec=OSRMRouter)
    router = CachingOSRMRouter(
        live_router=live_osrm_router,
        route_cache=route_cache,
    )

    candidate = Candidate(
        station=camp_verde_station,
        route_index=5,
        along_route_km=145.0,
        offroute_km=1.0,
    )

    detour_min = router.detour_min(
        direct_route,
        candidate,
    )

    assert detour_min == pytest.approx(12.0)
    live_osrm_router.route_through.assert_not_called()
