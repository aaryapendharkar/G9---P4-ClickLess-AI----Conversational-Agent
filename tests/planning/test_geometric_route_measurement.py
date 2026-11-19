"""Regression tests for deterministic geometric route measurements."""

import pytest

from clickless.contracts import Place
from clickless.planning import route_measurement


def _fixture_route(pipe_a):
    """Load the working Phoenix-to-Flagstaff cached route."""

    return pipe_a.router.route(
        Place(
            text="Phoenix",
            lat=33.4484,
            lon=-112.0740,
        ),
        Place(
            text="Flagstaff",
            lat=35.1983,
            lon=-111.6513,
        ),
    )


def test_station_at_destination_is_clamped_to_route_end(
    pipe_a,
    cfg_a,
    monkeypatch,
):
    """Negligible projection rounding must not place a stop after destination."""

    route = _fixture_route(pipe_a)
    geometry = [(point.lat, point.lon) for point in route.geometry]
    geometry_distance_km = route_measurement.polyline_length_km(geometry)

    destination_station = pipe_a.store.station("FIX-ST-004").model_copy(
        update={
            "station_id": "DESTINATION-STATION",
            "name": "Destination station",
            "lat": route.destination.lat,
            "lon": route.destination.lon,
        }
    )

    # Reproduce the real snapshot failure deterministically. The projection
    # accumulated route segments to a value a tiny fraction above the geometry
    # length calculated through Python's compensated sum.
    monkeypatch.setattr(
        route_measurement,
        "nearest_on_polyline",
        lambda _lat, _lon, _geometry: (
            0.0,
            len(geometry) - 2,
            geometry_distance_km + 1e-12,
        ),
    )

    measurement = route_measurement.measure_itinerary_route(
        route,
        [destination_station],
        cfg_a["energy"],
    )

    measured_station = measurement.points[1]
    final_leg = measurement.legs[-1]

    assert measured_station.along_road_km == route.distance_km
    assert final_leg.distance_km == pytest.approx(0.0)


def test_genuinely_reversed_stations_still_raise(
    pipe_a,
    cfg_a,
    monkeypatch,
):
    """Clamping route endpoints must not hide a real stop-order violation."""

    route = _fixture_route(pipe_a)
    geometry = [(point.lat, point.lon) for point in route.geometry]
    geometry_distance_km = route_measurement.polyline_length_km(geometry)

    first_station = pipe_a.store.station("FIX-ST-001").model_copy(
        update={"name": "First station"}
    )
    second_station = pipe_a.store.station("FIX-ST-004").model_copy(
        update={"name": "Second station"}
    )

    projected_positions = iter(
        [
            geometry_distance_km * 0.75,
            geometry_distance_km * 0.25,
        ]
    )

    monkeypatch.setattr(
        route_measurement,
        "nearest_on_polyline",
        lambda _lat, _lon, _geometry: (
            0.0,
            0,
            next(projected_positions),
        ),
    )

    with pytest.raises(
        ValueError,
        match="'Second station' appears before 'First station'",
    ):
        route_measurement.measure_itinerary_route(
            route,
            [first_station, second_station],
            cfg_a["energy"],
        )


def test_measurer_reuses_route_and_station_calculations(
    pipe_a,
    cfg_a,
    monkeypatch,
):
    """Repeated itineraries calculate each active-route value only once."""

    route = _fixture_route(pipe_a).model_copy(
        update={"route_id": "measurement-cache-route"}
    )
    first_station = pipe_a.store.station("FIX-ST-001")
    second_station = pipe_a.store.station("FIX-ST-004")

    original_polyline_length = route_measurement.polyline_length_km
    original_nearest = route_measurement.nearest_on_polyline
    polyline_length_calls = 0
    station_projection_calls = 0

    def count_polyline_length(geometry):
        nonlocal polyline_length_calls
        polyline_length_calls += 1
        return original_polyline_length(geometry)

    def count_station_projection(lat, lon, geometry):
        nonlocal station_projection_calls
        station_projection_calls += 1
        return original_nearest(lat, lon, geometry)

    monkeypatch.setattr(
        route_measurement,
        "polyline_length_km",
        count_polyline_length,
    )
    monkeypatch.setattr(
        route_measurement,
        "nearest_on_polyline",
        count_station_projection,
    )

    measurer = route_measurement.GeometricRouteMeasurer(cfg_a["energy"])

    first_measurement = measurer.measure(
        route,
        [first_station],
    )
    second_measurement = measurer.measure(
        route,
        [first_station, second_station],
    )
    repeated_measurement = measurer.measure(
        route,
        [first_station],
    )

    assert polyline_length_calls == 1
    assert station_projection_calls == 2
    assert repeated_measurement == first_measurement
    assert len(second_measurement.legs) == 3


def test_measurer_rebuilds_cache_when_geometry_changes(
    pipe_a,
    cfg_a,
    monkeypatch,
):
    """A changed geometry must not reuse projections from an older route."""

    route = _fixture_route(pipe_a).model_copy(
        update={"route_id": "changing-geometry-route"}
    )
    station = pipe_a.store.station("FIX-ST-004")
    original_nearest = route_measurement.nearest_on_polyline
    station_projection_calls = 0

    def count_station_projection(lat, lon, geometry):
        nonlocal station_projection_calls
        station_projection_calls += 1
        return original_nearest(lat, lon, geometry)

    monkeypatch.setattr(
        route_measurement,
        "nearest_on_polyline",
        count_station_projection,
    )

    measurer = route_measurement.GeometricRouteMeasurer(cfg_a["energy"])
    measurer.measure(route, [station])

    shifted_geometry = [
        point.model_copy(update={"lat": point.lat + 0.001}) for point in route.geometry
    ]
    changed_route = route.model_copy(update={"geometry": shifted_geometry})
    measurer.measure(changed_route, [station])

    assert station_projection_calls == 2
