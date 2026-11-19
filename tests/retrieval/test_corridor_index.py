"""The indexed corridor retrieval returns exactly what the original brute force returned."""

import random
from datetime import datetime, timezone

import pytest

from clickless.common.geo import haversine_km, nearest_on_polyline
from clickless.contracts import (
    Amenity,
    AmenityCategory,
    Candidate,
    ChargerLevel,
    ChargingPort,
    Connector,
    DataStatus,
    LatLon,
    NearbyAmenity,
    Provenance,
    Route,
    Station,
    StationStatus,
    TripRequest,
)
from clickless.data import DataStore
from clickless.retrieval.corridor import CorridorRetriever

PROV = Provenance(source="SYNTHETIC", retrieved_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
                  status=DataStatus.STATIC)


def brute_force(store, route, buffer_km, amenity_radius_m, max_candidates):
    """The original implementation, kept verbatim as the reference."""
    poly = [(p.lat, p.lon) for p in route.geometry]
    out = []
    for st in store.stations.values():
        off_km, seg, along = nearest_on_polyline(st.lat, st.lon, poly)
        if off_km > buffer_km:
            continue
        nearby = [
            NearbyAmenity(amenity=a, distance_m=d)
            for a in store.amenities
            if (d := haversine_km(st.lat, st.lon, a.lat, a.lon) * 1000) <= amenity_radius_m
        ]
        out.append(Candidate(station=st, route_index=seg, along_route_km=along, offroute_km=off_km, amenities=nearby))
    out.sort(key=lambda c: c.along_route_km)
    return out[:max_candidates]


def _summary(cands):
    return [(c.station.station_id, c.route_index, c.along_route_km, c.offroute_km,
             [(n.amenity.amenity_id, n.distance_m) for n in c.amenities]) for c in cands]


def _world(seed, lat0, lon0, n_stations=1500, n_amenities=4000, n_points=60):
    rng = random.Random(seed)
    # a wiggly route of about 150-300 km with uneven segment lengths, like OSRM overview geometry
    pts, lat, lon = [], lat0, lon0
    for _ in range(n_points):
        pts.append(LatLon(lat=lat, lon=lon))
        lat += rng.uniform(-0.01, 0.05)
        lon += rng.uniform(0.0, 0.08) * rng.choice([1, 1, 1, 6])
    stations, amenities = [], []
    for i in range(n_stations):
        if i % 3 == 0:  # near the route, some right at the buffer edge
            p = rng.choice(pts)
            lat, lon = p.lat + rng.uniform(-0.09, 0.09), p.lon + rng.uniform(-0.12, 0.12)
        else:
            lat, lon = lat0 + rng.uniform(-1.5, 3.5), lon0 + rng.uniform(-1.5, 6.5)
        stations.append(Station(station_id=f"s{rng.randrange(10**9)}-{i}", name=f"S{i}", lat=lat, lon=lon,
                                status=StationStatus.AVAILABLE, provenance=PROV,
                                ports=[ChargingPort(connector=Connector.CCS1, level=ChargerLevel.DC_FAST,
                                                    power_kw=150.0)]))
    for i in range(n_amenities):
        st = rng.choice(stations)
        lat, lon = st.lat + rng.uniform(-0.006, 0.006), st.lon + rng.uniform(-0.008, 0.008)
        amenities.append(Amenity(amenity_id=f"a{i}", name=None, category=AmenityCategory.FOOD, lat=lat, lon=lon,
                                 provenance=PROV))
    rng.shuffle(stations)
    store = DataStore(stations, [], amenities, {}, None)
    route = Route(route_id="r", origin=pts[0], destination=pts[-1], geometry=pts, distance_km=1.0,
                  duration_min=1.0, provenance=PROV)
    return store, route


@pytest.mark.parametrize("seed, lat0, lon0", [(1, 33.4, -112.1), (2, 40.7, -74.0), (3, 61.2, -149.9),
                                             (4, 19.6, -155.5), (5, 47.6, -122.3)])
@pytest.mark.parametrize("buffer_km, max_candidates", [(8, 60), (8, 10_000), (2, 10_000), (25, 10_000)])
def test_same_candidates_as_brute_force(seed, lat0, lon0, buffer_km, max_candidates):
    store, route = _world(seed, lat0, lon0)
    request = TripRequest(request_id="x", raw_text="x")
    fast = CorridorRetriever(store, buffer_km, 400, max_candidates).candidates(route, request)
    assert _summary(fast) == _summary(brute_force(store, route, buffer_km, 400, max_candidates))
    assert fast  # the worlds put stations near the route


def test_single_point_route_and_index_rebuild():
    store, route = _world(9, 33.4, -112.1)
    one = route.model_copy(update={"geometry": route.geometry[:1]})
    request = TripRequest(request_id="x", raw_text="x")
    retriever = CorridorRetriever(store, 8, 400, 10_000)
    assert _summary(retriever.candidates(one, request)) == _summary(brute_force(store, one, 8, 400, 10_000))
    store.amenities.append(Amenity(amenity_id="late", name=None, category=AmenityCategory.FOOD,
                                   lat=route.geometry[0].lat, lon=route.geometry[0].lon, provenance=PROV))
    assert _summary(retriever.candidates(route, request)) == _summary(brute_force(store, route, 8, 400, 10_000))


@pytest.mark.parametrize("seed, lat0, lon0", [(11, 47.6, -122.3), (12, 35.0, -105.0)])
def test_long_route_same_as_brute_force(seed, lat0, lon0):
    """A long trip: 2,000 route points with short and long segments, like OSRM overview geometry."""
    store, route = _world(seed, lat0, lon0, n_stations=1200, n_amenities=2000, n_points=2000)
    request = TripRequest(request_id="x", raw_text="x")
    for buffer_km in (8, 3):
        fast = CorridorRetriever(store, buffer_km, 400, 10_000).candidates(route, request)
        assert _summary(fast) == _summary(brute_force(store, route, buffer_km, 400, 10_000))
