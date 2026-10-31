"""Offline tests for routing clients.

These tests use simulated HTTP responses. They must never contact the public
OSRM demo server during the normal test suite.
"""

from copy import deepcopy
from unittest.mock import Mock

import pytest
import requests

from clickless.config import repo_path
from clickless.contracts import Candidate, DataStatus, Place
from clickless.pipeline import _build_router
from clickless.planning.routing import (
    CachedRouter,
    CachingOSRMRouter,
    OSRMRouteError,
    OSRMRouter,
)


def _phoenix_and_flagstaff() -> tuple[Place, Place]:
    """Return readable test places with coordinates required by OSRM."""

    origin = Place(
        text="Phoenix",
        lat=33.4484,
        lon=-112.0740,
    )
    destination = Place(
        text="Flagstaff",
        lat=35.1983,
        lon=-111.6513,
    )

    return origin, destination


def _router_with_payload(
    response_payload: dict,
) -> tuple[OSRMRouter, Mock, Mock]:
    """Build an OSRM router whose HTTP response is fully controlled by a test."""

    mock_http_session = Mock()
    mock_http_response = Mock()

    mock_http_response.url = (
        "https://router.project-osrm.org/route/v1/driving/"
        "-112.074000,33.448400;-111.651300,35.198300"
    )
    mock_http_response.json.return_value = response_payload
    mock_http_session.get.return_value = mock_http_response

    router = OSRMRouter(
        base_url="https://router.project-osrm.org",
        http_session=mock_http_session,
    )

    return router, mock_http_session, mock_http_response


def test_osrm_router_converts_successful_response_to_route():
    """OSRM meters, seconds, and lon/lat geometry become our Route fields."""

    # A mock session behaves like requests.Session without using the network.
    mock_http_session = Mock()
    mock_http_response = Mock()

    mock_http_response.url = (
        "https://router.project-osrm.org/route/v1/driving/"
        "-112.074000,33.448400;-111.651300,35.198300"
    )
    mock_http_response.json.return_value = {
        "code": "Ok",
        "routes": [
            {
                "distance": 233_000.0,
                "duration": 8_400.0,
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [-112.0740, 33.4484],
                        [-111.9000, 34.5000],
                        [-111.6513, 35.1983],
                    ],
                },
            }
        ],
    }
    mock_http_session.get.return_value = mock_http_response

    origin = Place(
        text="Phoenix",
        lat=33.4484,
        lon=-112.0740,
    )
    destination = Place(
        text="Flagstaff",
        lat=35.1983,
        lon=-111.6513,
    )

    router = OSRMRouter(
        base_url="https://router.project-osrm.org",
        http_session=mock_http_session,
    )

    route = router.route(origin, destination)

    # OSRM reports meters and seconds. Our Route contract uses kilometres
    # and minutes, so the client must convert both units.
    assert route.distance_km == pytest.approx(233.0)
    assert route.duration_min == pytest.approx(140.0)

    assert route.origin.lat == pytest.approx(33.4484)
    assert route.origin.lon == pytest.approx(-112.0740)
    assert route.destination.lat == pytest.approx(35.1983)
    assert route.destination.lon == pytest.approx(-111.6513)

    # GeoJSON uses [longitude, latitude]. Our LatLon class deliberately stores
    # named lat/lon fields, preventing the two values from being confused.
    assert route.geometry[1].lat == pytest.approx(34.5)
    assert route.geometry[1].lon == pytest.approx(-111.9)

    assert route.via == []
    assert route.provenance.source == "OSRM"
    assert route.provenance.status == DataStatus.RUNTIME
    assert route.provenance.source_url == mock_http_response.url

    mock_http_session.get.assert_called_once()

    requested_url = mock_http_session.get.call_args.args[0]
    request_options = mock_http_session.get.call_args.kwargs["params"]

    # OSRM requires longitude before latitude in URL coordinates.
    assert "-112.074000,33.448400" in requested_url
    assert "-111.651300,35.198300" in requested_url

    assert request_options == {
        "overview": "full",
        "geometries": "geojson",
        "steps": "false",
    }
    assert mock_http_session.get.call_args.kwargs["timeout"] == 10.0


def test_osrm_router_reports_no_route():
    """An explicit OSRM NoRoute result must not become a geometric fallback."""

    router, _mock_http_session, _mock_http_response = _router_with_payload(
        {
            "code": "NoRoute",
            "message": "No route found between points",
        }
    )
    origin, destination = _phoenix_and_flagstaff()

    with pytest.raises(
        OSRMRouteError,
        match="NoRoute.*No route found",
    ):
        router.route(origin, destination)


def test_osrm_router_calculates_detour_from_station_route(store):
    """Detour is the station-route duration minus the direct-route duration."""

    direct_route_payload = {
        "code": "Ok",
        "routes": [
            {
                "distance": 233_000.0,
                "duration": 8_400.0,
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [-112.0740, 33.4484],
                        [-111.6513, 35.1983],
                    ],
                },
            }
        ],
    }
    router, mock_http_session, mock_http_response = _router_with_payload(
        direct_route_payload
    )
    origin, destination = _phoenix_and_flagstaff()

    direct_route = router.route(
        origin,
        destination,
    )

    # The second response represents the complete route through Camp Verde.
    # It takes 152 minutes instead of the direct route's 140 minutes.
    mock_http_response.url = (
        "https://router.project-osrm.org/route/v1/driving/"
        "-112.074000,33.448400;"
        "-111.860000,34.565000;"
        "-111.651300,35.198300"
    )
    mock_http_response.json.return_value = {
        "code": "Ok",
        "routes": [
            {
                "distance": 241_000.0,
                "duration": 9_120.0,
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [-112.0740, 33.4484],
                        [-111.8600, 34.5650],
                        [-111.6513, 35.1983],
                    ],
                },
            }
        ],
    }

    camp_verde_station = store.station("FIX-ST-004")
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
    assert mock_http_session.get.call_count == 2

    station_route_url = mock_http_session.get.call_args_list[1].args[0]
    expected_station_coordinate = (
        f"{camp_verde_station.lon:.6f}," f"{camp_verde_station.lat:.6f}"
    )

    assert expected_station_coordinate in station_route_url

    # Two semicolons mean the request contains three ordered coordinates:
    # origin, charging station, and destination.
    assert station_route_url.count(";") == 2


def test_osrm_router_wraps_timeout():
    """Network failures become routing errors with a useful explanation."""

    mock_http_session = Mock()
    mock_http_session.get.side_effect = requests.Timeout(
        "public OSRM server did not respond"
    )

    router = OSRMRouter(
        base_url="https://router.project-osrm.org",
        http_session=mock_http_session,
    )
    origin, destination = _phoenix_and_flagstaff()

    with pytest.raises(
        OSRMRouteError,
        match="timed out",
    ):
        router.route(origin, destination)


def test_osrm_router_requires_coordinates():
    """A text-only place cannot be sent to a coordinate-based routing API."""

    mock_http_session = Mock()
    router = OSRMRouter(
        base_url="https://router.project-osrm.org",
        http_session=mock_http_session,
    )

    origin_without_coordinates = Place(text="Phoenix")
    _origin, destination = _phoenix_and_flagstaff()

    with pytest.raises(
        ValueError,
        match="Phoenix.*latitude and longitude",
    ):
        router.route(
            origin_without_coordinates,
            destination,
        )

    # Input validation happens before any network request is attempted.
    mock_http_session.get.assert_not_called()


def test_osrm_router_rejects_empty_routes():
    """An Ok response is invalid when it contains no actual route."""

    router, _mock_http_session, _mock_http_response = _router_with_payload(
        {
            "code": "Ok",
            "routes": [],
        }
    )
    origin, destination = _phoenix_and_flagstaff()

    with pytest.raises(
        OSRMRouteError,
        match="included no routes",
    ):
        router.route(origin, destination)


def test_osrm_router_rejects_incomplete_geometry():
    """A usable route requires at least an origin and destination point."""

    router, _mock_http_session, _mock_http_response = _router_with_payload(
        {
            "code": "Ok",
            "routes": [
                {
                    "distance": 233_000.0,
                    "duration": 8_400.0,
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [
                            [-112.0740, 33.4484],
                        ],
                    },
                }
            ],
        }
    )
    origin, destination = _phoenix_and_flagstaff()

    with pytest.raises(
        OSRMRouteError,
        match="at least two points",
    ):
        router.route(origin, destination)


def test_osrm_router_rejects_invalid_json():
    """A successful HTTP response still needs a valid OSRM JSON body."""

    router, _mock_http_session, mock_http_response = _router_with_payload({})
    mock_http_response.json.side_effect = ValueError("invalid JSON")

    origin, destination = _phoenix_and_flagstaff()

    with pytest.raises(
        OSRMRouteError,
        match="not valid JSON",
    ):
        router.route(origin, destination)


def test_osrm_router_never_returns_negative_detour(store):
    """A station route reported as slightly faster produces a zero detour."""

    router, _mock_http_session, mock_http_response = _router_with_payload(
        {
            "code": "Ok",
            "routes": [
                {
                    "distance": 233_000.0,
                    "duration": 8_400.0,
                    "geometry": {
                        "type": "LineString",
                        "coordinates": [
                            [-112.0740, 33.4484],
                            [-111.6513, 35.1983],
                        ],
                    },
                }
            ],
        }
    )
    origin, destination = _phoenix_and_flagstaff()
    direct_route = router.route(origin, destination)

    mock_http_response.json.return_value = {
        "code": "Ok",
        "routes": [
            {
                "distance": 233_000.0,
                "duration": 8_399.99,
                "geometry": {
                    "type": "LineString",
                    "coordinates": [
                        [-112.0740, 33.4484],
                        [-111.8600, 34.5650],
                        [-111.6513, 35.1983],
                    ],
                },
            }
        ],
    }

    candidate = Candidate(
        station=store.station("FIX-ST-004"),
        route_index=5,
        along_route_km=145.0,
        offroute_km=1.0,
    )

    assert (
        router.detour_min(
            direct_route,
            candidate,
        )
        == 0.0
    )


def test_build_router_uses_cached_router_for_cache_mode(
    store,
    cfg_a,
):
    """The normal offline configuration must not construct a live client."""

    test_config = deepcopy(cfg_a)
    test_config["routing"]["mode"] = "cache"

    router = _build_router(
        test_config,
        store,
    )

    assert isinstance(router, CachedRouter)


def test_build_router_uses_default_osrm_cache_directory(
    store,
    cfg_a,
    monkeypatch,
):
    """OSRM mode should default to the repository's ignored cache directory."""

    test_config = deepcopy(cfg_a)
    test_config["routing"]["mode"] = "osrm"
    test_config["routing"]["osrm_base_url_env"] = "CLICKLESS_TEST_OSRM_URL"
    test_config["routing"].pop(
        "osrm_cache_dir",
        None,
    )

    monkeypatch.setenv(
        "CLICKLESS_TEST_OSRM_URL",
        "https://router.project-osrm.org",
    )

    router = _build_router(
        test_config,
        store,
    )

    assert isinstance(router, CachingOSRMRouter)
    assert router.route_cache.cache_directory == repo_path("data/cache/osrm")


def test_build_router_constructs_cache_first_osrm_router(
    store,
    cfg_a,
    monkeypatch,
    tmp_path,
):
    """OSRM mode should check a local cache before using the live client."""

    test_config = deepcopy(cfg_a)
    test_config["routing"]["mode"] = "osrm"
    test_config["routing"]["osrm_base_url_env"] = "CLICKLESS_TEST_OSRM_URL"
    test_config["routing"]["osrm_cache_dir"] = str(tmp_path / "osrm")

    monkeypatch.setenv(
        "CLICKLESS_TEST_OSRM_URL",
        "https://router.project-osrm.org/",
    )

    router = _build_router(
        test_config,
        store,
    )

    assert isinstance(router, CachingOSRMRouter)
    assert isinstance(router.live_router, OSRMRouter)

    # OSRMRouter removes the trailing slash so URL construction does not
    # accidentally produce a double slash.
    assert router.live_router.base_url == "https://router.project-osrm.org"

    # Tests use an isolated temporary cache rather than writing into the
    # repository's ignored runtime-data directory.
    assert router.route_cache.cache_directory == tmp_path / "osrm"


def test_build_router_rejects_missing_osrm_environment_variable(
    store,
    cfg_a,
    monkeypatch,
):
    """OSRM mode must fail clearly when its configured URL is unavailable."""

    test_config = deepcopy(cfg_a)
    test_config["routing"]["mode"] = "osrm"
    test_config["routing"]["osrm_base_url_env"] = "MISSING_TEST_OSRM_URL"

    monkeypatch.delenv(
        "MISSING_TEST_OSRM_URL",
        raising=False,
    )

    with pytest.raises(
        ValueError,
        match="MISSING_TEST_OSRM_URL",
    ):
        _build_router(
            test_config,
            store,
        )


def test_build_router_rejects_unknown_routing_mode(
    store,
    cfg_a,
):
    """Configuration spelling errors must not silently select OSRM."""

    test_config = deepcopy(cfg_a)
    test_config["routing"]["mode"] = "osmrr"

    with pytest.raises(
        ValueError,
        match="unsupported routing mode 'osmrr'",
    ):
        _build_router(
            test_config,
            store,
        )


def test_build_router_requires_osrm_environment_field(
    store,
    cfg_a,
):
    """OSRM mode must name the environment variable containing its URL."""

    test_config = deepcopy(cfg_a)
    test_config["routing"]["mode"] = "osrm"
    test_config["routing"].pop(
        "osrm_base_url_env",
        None,
    )

    with pytest.raises(
        ValueError,
        match="osrm_base_url_env",
    ):
        _build_router(
            test_config,
            store,
        )
