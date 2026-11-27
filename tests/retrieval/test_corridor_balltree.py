"""The BallTree corridor prototype (issue #190) must return exactly what the brute force returns.

Same contract as the grid's equivalence tests in test_corridor.py: identical stations, order,
floats and amenities, on the fixture routes, an adversarial world at several latitudes and buffers,
a degenerate route, a changed store and the frozen snapshot routes.
"""

import json
import random
from pathlib import Path

import pytest

from clickless.config import repo_path
from clickless.contracts import AmenityCategory, AmenityPreference, LatLon, Provenance, Route, TripRequest
from clickless.data import DataStore
from clickless.retrieval.corridor import CorridorRetriever
from clickless.retrieval.corridor_balltree import BallTreeCorridorRetriever

SNAPSHOT = repo_path("data/snapshot")
SNAPSHOT_ROUTES = (
    "san-jose-ca__palo-alto-ca.json",
    "mesa-az__glendale-az.json",
    "flagstaff-az__page-az.json",
)
CENTERS = {
    "phoenix": (34.1, -111.8),
    "anchorage": (61.2, -149.9),
    "honolulu": (21.3, -157.9),
    "northern-maine": (46.9, -68.0),
}


def _dump(cands):
    return [c.model_dump(mode="json") for c in cands]


def _load_route(path: Path) -> Route:
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("_meta", None)
    return Route.model_validate(payload)


def _route(points, rid="r"):
    geo = [LatLon(lat=a, lon=b) for a, b in points]
    prov = Provenance(source="SYNTHETIC", retrieved_at="2026-11-27T00:00:00Z", status="cached")
    return Route(
        route_id=rid,
        origin=geo[0],
        destination=geo[-1],
        geometry=geo,
        distance_km=1.0,
        duration_min=1.0,
        provenance=prov,
    )


def _assert_identical(store, route, buffer_km=8.0, radius_m=400.0, max_candidates=60, request=None):
    brute = CorridorRetriever(store, buffer_km, radius_m, max_candidates, method="brute_force")
    grid = CorridorRetriever(store, buffer_km, radius_m, max_candidates)
    tree = BallTreeCorridorRetriever(store, buffer_km, radius_m, max_candidates)
    expected = _dump(brute.candidates(route, request))
    assert _dump(tree.candidates(route, request)) == expected
    assert _dump(tree.candidates(route, request)) == expected  # memoized scan, fresh candidates
    assert _dump(grid.candidates(route, request)) == expected
    return expected


def test_balltree_rejects_brute_force_method(store):
    with pytest.raises(ValueError, match="indexed"):
        BallTreeCorridorRetriever(store, 8, 400, 60, method="brute_force")


@pytest.mark.parametrize("max_candidates", [60, 2, 10_000])
def test_balltree_equals_brute_force_on_fixture_routes(store, max_candidates):
    for path in sorted(repo_path("data/fixtures/routes").glob("*.json")):
        assert _assert_identical(store, _load_route(path), max_candidates=max_candidates)


@pytest.mark.parametrize("center", sorted(CENTERS))
def test_balltree_equals_brute_force_on_adversarial_world(store, center):
    """Stations near the buffer edge, on route vertices and duplicated (ties), at several latitudes."""
    lat0, lon0 = CENTERS[center]
    rng = random.Random(f"190-{center}")
    base = list(store.stations.values())
    offsets = [(-0.7, -0.3), (-0.5, -0.2), (-0.5, -0.2), (-0.1, -0.1), (0.2, 0.4), (0.2, 0.4), (0.7, 0.1)]
    route = _route([(lat0 + a, lon0 + b) for a, b in offsets])
    stations = []
    for k in range(600):
        lat, lon = lat0 + rng.uniform(-0.8, 0.8), lon0 + rng.uniform(-0.6, 0.6)
        if k % 5 == 0:
            vertex = route.geometry[k % len(route.geometry)]
            lat, lon = vertex.lat, vertex.lon
        stations.append(base[k % len(base)].model_copy(update={"station_id": f"adv-{k}", "lat": lat, "lon": lon}))
        if k % 7 == 0:
            stations.append(stations[-1].model_copy(update={"station_id": f"adv-{k}-dup"}))
    amenities = [
        a.model_copy(
            update={
                "amenity_id": f"am-{j}",
                "lat": s.lat + rng.uniform(-0.005, 0.005),
                "lon": s.lon + rng.uniform(-0.005, 0.005),
            }
        )
        for j, (s, a) in enumerate(zip(stations, store.amenities * 1000, strict=False))
    ]
    world = DataStore(stations, list(store.vehicles.values()), amenities, dict(store.places), store.routes_dir)
    for buffer_km in (0.5, 8.0, 25.0):
        assert _assert_identical(world, route, buffer_km=buffer_km, max_candidates=10_000)


def test_balltree_single_point_route_matches_brute_force(store):
    assert _assert_identical(store, _route([(35.0, -111.7)])) == []


def test_balltree_rebuilds_its_tree_when_the_store_changes(store):
    world = DataStore(
        list(store.stations.values()),
        list(store.vehicles.values()),
        list(store.amenities),
        dict(store.places),
        store.routes_dir,
    )
    route = _load_route(repo_path("data/fixtures/routes/phoenix__flagstaff.json"))
    tree = BallTreeCorridorRetriever(world, 8.0, 400.0, 60)
    first = tree.candidates(route, None)[0].station
    moved = first.model_copy(update={"lat": first.lat + 3.0})
    world.stations = {sid: (moved if sid == first.station_id else st) for sid, st in world.stations.items()}
    world.amenities = world.amenities[:1]
    after = tree.candidates(route, None)
    assert first.station_id not in {c.station.station_id for c in after}
    brute = CorridorRetriever(world, 8.0, 400.0, 60, method="brute_force")
    assert _dump(after) == _dump(brute.candidates(route, None))


def test_balltree_keeps_required_amenity_extras(store):
    """The post-scan steps are inherited, so a required accessible restroom is kept the same way."""
    required = AmenityPreference(category=AmenityCategory.RESTROOM, wheelchair_required=True, priority="important")
    route = _load_route(repo_path("data/fixtures/routes/phoenix__flagstaff.json"))
    trip = TripRequest(request_id="r190", raw_text="need an accessible restroom", amenities=[required])
    brute = CorridorRetriever(store, 8.0, 400.0, 1, method="brute_force", required_extra_per_bin=2)
    tree = BallTreeCorridorRetriever(store, 8.0, 400.0, 1, required_extra_per_bin=2)
    expected = _dump(brute.candidates(route, trip))
    assert len(expected) == 2  # the cap keeps 1, the required restroom adds the second
    assert _dump(tree.candidates(route, trip)) == expected


def _snapshot_subset(tmp_path: Path, route: Route, pad_deg: float = 0.2) -> DataStore:
    lats = [p.lat for p in route.geometry]
    lons = [p.lon for p in route.geometry]
    lo_lat, hi_lat, lo_lon, hi_lon = min(lats) - pad_deg, max(lats) + pad_deg, min(lons) - pad_deg, max(lons) + pad_deg
    for name, pad in (("stations.json", 0.0), ("amenities.json", 0.05), ("vehicles.json", None), ("places.json", None)):
        data = json.loads((SNAPSHOT / name).read_text(encoding="utf-8"))
        if pad is not None:
            data["records"] = [
                r
                for r in data["records"]
                if lo_lat - pad <= r["lat"] <= hi_lat + pad and lo_lon - pad <= r["lon"] <= hi_lon + pad
            ]
        (tmp_path / name).write_text(json.dumps(data), encoding="utf-8")
    return DataStore.from_dir(tmp_path)


@pytest.mark.skipif(
    not (SNAPSHOT / "MANIFEST.json").is_file() or not (SNAPSHOT / "routes").is_dir(),
    reason="needs the downloaded snapshot with its route cache (#36, #92)",
)
@pytest.mark.parametrize("route_file", SNAPSHOT_ROUTES)
def test_balltree_equals_brute_force_on_snapshot_routes(tmp_path, route_file):
    path = SNAPSHOT / "routes" / route_file
    if not path.is_file():
        pytest.skip(f"{route_file} not in this snapshot's route cache")
    route = _load_route(path)
    world = _snapshot_subset(tmp_path, route)
    assert _assert_identical(world, route, max_candidates=10_000)
    assert _assert_identical(world, route, max_candidates=60)
