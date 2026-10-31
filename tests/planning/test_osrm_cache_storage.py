"""Test OSRM cache keys and local file reading and writing.

These tests use temporary directories and never contact an OSRM server.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from clickless.contracts import (
    DataStatus,
    LatLon,
    Provenance,
    Route,
)
from clickless.planning.route_cache import (
    OSRMRouteCache,
    OSRMRouteCacheError,
    build_osrm_cache_key,
)


def _phoenix_and_flagstaff_coordinates() -> tuple[LatLon, LatLon]:
    """Return readable coordinates shared by the cache-key tests."""

    phoenix = LatLon(
        lat=33.4484,
        lon=-112.0740,
    )
    flagstaff = LatLon(
        lat=35.1983,
        lon=-111.6513,
    )

    return phoenix, flagstaff


def _runtime_osrm_route() -> Route:
    """Return a small validated route resembling a live OSRM response."""

    phoenix, flagstaff = _phoenix_and_flagstaff_coordinates()

    return Route(
        route_id="osrm-test-route",
        origin=phoenix,
        destination=flagstaff,
        geometry=[
            phoenix,
            LatLon(
                lat=34.5,
                lon=-111.9,
            ),
            flagstaff,
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


def test_same_route_request_produces_same_cache_key():
    """Identical ordered requests must reuse the same cache entry."""

    phoenix, flagstaff = _phoenix_and_flagstaff_coordinates()

    first_key = build_osrm_cache_key([phoenix, flagstaff])
    repeated_key = build_osrm_cache_key([phoenix, flagstaff])

    assert first_key == repeated_key


def test_reversing_route_points_changes_cache_key():
    """Driving in the opposite direction is a different route request."""

    phoenix, flagstaff = _phoenix_and_flagstaff_coordinates()

    outbound_key = build_osrm_cache_key([phoenix, flagstaff])
    return_key = build_osrm_cache_key([flagstaff, phoenix])

    assert outbound_key != return_key


def test_waypoint_changes_cache_key():
    """A required charging stop must not reuse the direct-route entry."""

    phoenix, flagstaff = _phoenix_and_flagstaff_coordinates()
    camp_verde_station = LatLon(
        lat=34.5636,
        lon=-111.8543,
    )

    direct_key = build_osrm_cache_key([phoenix, flagstaff])
    waypoint_key = build_osrm_cache_key(
        [
            phoenix,
            camp_verde_station,
            flagstaff,
        ]
    )

    assert direct_key != waypoint_key


def test_cache_key_rejects_fewer_than_two_route_points():
    """OSRM cannot route without both an origin and destination."""

    phoenix, _ = _phoenix_and_flagstaff_coordinates()

    with pytest.raises(
        ValueError,
        match="at least an origin and destination",
    ):
        build_osrm_cache_key([phoenix])


def test_missing_cache_entry_returns_none(tmp_path: Path):
    """A normal cache miss must not be reported as corruption."""

    phoenix, flagstaff = _phoenix_and_flagstaff_coordinates()
    route_cache = OSRMRouteCache(tmp_path / "osrm")

    cached_route = route_cache.load([phoenix, flagstaff])

    assert cached_route is None


def test_saved_route_can_be_loaded_from_cache(tmp_path: Path):
    """A saved OSRM route must survive a complete write-and-read cycle."""

    phoenix, flagstaff = _phoenix_and_flagstaff_coordinates()
    runtime_route = _runtime_osrm_route()
    route_cache = OSRMRouteCache(tmp_path / "osrm")

    cache_entry_path = route_cache.save(
        [phoenix, flagstaff],
        runtime_route,
    )
    cached_route = route_cache.load([phoenix, flagstaff])

    assert cache_entry_path.exists()
    assert cached_route is not None
    assert cached_route.route_id == runtime_route.route_id
    assert cached_route.distance_km == runtime_route.distance_km
    assert cached_route.duration_min == runtime_route.duration_min
    assert cached_route.provenance.source == "OSRM"
    assert cached_route.provenance.status == DataStatus.CACHED

    # Keep the original retrieval time so users can tell how old the cached
    # OSRM measurement is.
    assert cached_route.provenance.retrieved_at == runtime_route.provenance.retrieved_at


def test_different_route_request_is_a_cache_miss(tmp_path: Path):
    """A waypoint request must not reuse the cached direct route."""

    phoenix, flagstaff = _phoenix_and_flagstaff_coordinates()
    camp_verde_station = LatLon(
        lat=34.5636,
        lon=-111.8543,
    )
    route_cache = OSRMRouteCache(tmp_path / "osrm")

    route_cache.save(
        [phoenix, flagstaff],
        _runtime_osrm_route(),
    )

    waypoint_route = route_cache.load(
        [
            phoenix,
            camp_verde_station,
            flagstaff,
        ]
    )

    assert waypoint_route is None


def test_corrupt_cache_entry_raises_readable_error(tmp_path: Path):
    """Malformed stored data must be reported instead of treated as a miss."""

    phoenix, flagstaff = _phoenix_and_flagstaff_coordinates()
    route_cache = OSRMRouteCache(tmp_path / "osrm")

    cache_entry_path = route_cache._entry_path([phoenix, flagstaff])
    cache_entry_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )
    cache_entry_path.write_text(
        "this is not valid route JSON",
        encoding="utf-8",
    )

    with pytest.raises(
        OSRMRouteCacheError,
        match="could not read OSRM cache entry",
    ):
        route_cache.load([phoenix, flagstaff])
