import json
import random
from pathlib import Path

import pytest

from clickless.config import repo_path
from clickless.contracts import AmenityCategory, AmenityPreference, LatLon, Provenance, Route
from clickless.data import DataStore
from clickless.retrieval.corridor import CorridorRetriever, polyline_km, select_distributed


def _route_and_req(pipe_a, text):
    req = pipe_a.parser.parse(text, "c1").request
    return pipe_a.router.route(req.origin, req.destination), req


def test_corridor_excludes_far_station(pipe_a, demo_text):
    route, req = _route_and_req(pipe_a, demo_text)
    ids = {c.station.station_id for c in pipe_a.retriever.candidates(route, req)}
    assert "FIX-ST-004" in ids and "FIX-ST-006" not in ids  # Sedona is outside the 8 km buffer


def test_candidates_ordered_along_route(pipe_a, demo_text):
    route, req = _route_and_req(pipe_a, demo_text)
    along = [c.along_route_km for c in pipe_a.retriever.candidates(route, req)]
    assert along == sorted(along)


def test_amenities_linked_within_radius(pipe_a, demo_text):
    route, req = _route_and_req(pipe_a, demo_text)
    cv = next(c for c in pipe_a.retriever.candidates(route, req) if c.station.station_id == "FIX-ST-004")
    assert {"fix:a05", "fix:a06"} <= {na.amenity.amenity_id for na in cv.amenities}
    assert all(na.distance_m <= 400 for na in cv.amenities)


@pytest.mark.xfail(reason="TODO(Manas): real TF-IDF should score related phrasing, not just category")
def test_tfidf_matches_cuisine_phrase(store):
    from clickless.retrieval.tfidf_matcher import TfidfMatcher
    kitchen = next(a for a in store.amenities if a.amenity_id == "fix:a05")
    burgers = next(a for a in store.amenities if a.amenity_id == "fix:a03")
    pref = AmenityPreference(category=AmenityCategory.FOOD, phrase="mexican food")
    m = TfidfMatcher(store)
    assert m.score(pref, kitchen) > m.score(pref, burgers)


# --------------------------------------------------------------------------- indexed == brute force (issue #94)

SNAPSHOT = repo_path("data/snapshot")
SNAPSHOT_ROUTES = (  # a dense urban, a mid-length suburban and a long sparse benchmark route
    "san-jose-ca__palo-alto-ca.json",
    "mesa-az__glendale-az.json",
    "flagstaff-az__page-az.json",
)


def _pair(store, buffer_km=8.0, radius_m=400.0, max_candidates=60):
    return (
        CorridorRetriever(store, buffer_km, radius_m, max_candidates, method="brute_force"),
        CorridorRetriever(store, buffer_km, radius_m, max_candidates, method="indexed"),
    )


def _dump(cands):
    return [c.model_dump(mode="json") for c in cands]


def _assert_identical(store, route, **kw):
    brute, fast = _pair(store, **kw)
    expected = _dump(brute.candidates(route, None))
    assert _dump(fast.candidates(route, None)) == expected  # same stations, order, floats, amenities
    assert _dump(fast.candidates(route, None)) == expected  # memoized amenity links give the same answer
    return expected


def _route(points, rid="r"):
    geo = [LatLon(lat=a, lon=b) for a, b in points]
    prov = Provenance(source="SYNTHETIC", retrieved_at="2026-11-19T00:00:00Z", status="cached")
    return Route(route_id=rid, origin=geo[0], destination=geo[-1], geometry=geo, distance_km=1.0,
                 duration_min=1.0, provenance=prov)


def test_unknown_method_rejected(store):
    with pytest.raises(ValueError, match="method"):
        CorridorRetriever(store, 8, 400, 60, method="balltree")


@pytest.mark.parametrize("max_candidates", [60, 2, 10_000])
def test_indexed_equals_brute_force_on_fixture_routes(store, max_candidates):
    for path in sorted(repo_path("data/fixtures/routes").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.pop("_meta", None)
        expected = _assert_identical(store, Route.model_validate(payload), max_candidates=max_candidates)
        assert expected  # the fixture corridor is not empty, so the comparison means something


# (lat, lon): mid-latitude desert, high latitude (wide longitude cells), tropics, far north-east
ADVERSARIAL_CENTERS = {
    "phoenix": (34.1, -111.8),
    "anchorage": (61.2, -149.9),
    "honolulu": (21.3, -157.9),
    "northern-maine": (46.9, -68.0),
}


@pytest.mark.parametrize("center", sorted(ADVERSARIAL_CENTERS))
def test_indexed_equals_brute_force_on_adversarial_world(store, center):
    """Jittered copies of the fixture stations near the buffer edge, exact duplicates (ties in
    along_route_km), stations on route vertices, and a route with repeated points, at several latitudes."""
    lat0, lon0 = ADVERSARIAL_CENTERS[center]
    rng = random.Random(f"94-{center}")
    base = list(store.stations.values())
    offsets = [(-0.7, -0.3), (-0.5, -0.2), (-0.5, -0.2), (-0.1, -0.1), (0.2, 0.4), (0.2, 0.4), (0.7, 0.1)]
    route = _route([(lat0 + a, lon0 + b) for a, b in offsets])
    stations = []
    for k in range(600):
        lat = lat0 + rng.uniform(-0.8, 0.8)
        lon = lon0 + rng.uniform(-0.6, 0.6)
        if k % 5 == 0:  # on a vertex
            lat, lon = (route.geometry[k % len(route.geometry)].lat, route.geometry[k % len(route.geometry)].lon)
        stations.append(base[k % len(base)].model_copy(update={"station_id": f"adv-{k}", "lat": lat, "lon": lon}))
        if k % 7 == 0:  # an exact duplicate position -> equal along_route_km, order decided by store order
            stations.append(stations[-1].model_copy(update={"station_id": f"adv-{k}-dup"}))
    amenities = [a.model_copy(update={"amenity_id": f"am-{j}", "lat": s.lat + rng.uniform(-0.005, 0.005),
                                      "lon": s.lon + rng.uniform(-0.005, 0.005)})
                 for j, (s, a) in enumerate(zip(stations, store.amenities * 1000, strict=False))]
    world = DataStore(stations, list(store.vehicles.values()), amenities, dict(store.places), store.routes_dir)
    for buffer_km in (0.5, 8.0, 25.0):
        expected = _assert_identical(world, route, buffer_km=buffer_km, max_candidates=10_000)
        assert expected and any(c["amenities"] for c in expected)


def test_single_point_route_matches_brute_force(store):
    route = _route([(35.0, -111.7)])
    assert _assert_identical(store, route) == []  # no segment, so no station is within the corridor


def test_index_rebuilt_when_the_store_changes(store):
    world = DataStore(list(store.stations.values()), list(store.vehicles.values()), list(store.amenities),
                      dict(store.places), store.routes_dir)
    payload = json.loads(repo_path("data/fixtures/routes/phoenix__flagstaff.json").read_text(encoding="utf-8"))
    payload.pop("_meta", None)
    route = Route.model_validate(payload)
    fast = CorridorRetriever(world, 8.0, 400.0, 60)
    before = fast.candidates(route, None)
    first = before[0].station
    # replace the station collection: the first candidate's station moves far off the route
    moved = first.model_copy(update={"lat": first.lat + 3.0})
    world.stations = {sid: (moved if sid == first.station_id else st) for sid, st in world.stations.items()}
    world.amenities = world.amenities[:1]  # and the amenity list shrinks
    after = fast.candidates(route, None)
    assert first.station_id not in {c.station.station_id for c in after}
    assert _dump(after) == _dump(_pair(world)[0].candidates(route, None))


def _snapshot_subset(tmp_path: Path, route: Route, pad_deg: float = 0.2) -> DataStore:
    """The snapshot's stations within the route bbox + pad_deg (~20 km), and every amenity that
    could be near them, written as a small snapshot-format directory and loaded normally."""
    lats = [p.lat for p in route.geometry]
    lons = [p.lon for p in route.geometry]
    lo_lat, hi_lat, lo_lon, hi_lon = min(lats) - pad_deg, max(lats) + pad_deg, min(lons) - pad_deg, max(lons) + pad_deg
    for name, pad in (("stations.json", 0.0), ("amenities.json", 0.05), ("vehicles.json", None), ("places.json", None)):
        data = json.loads((SNAPSHOT / name).read_text(encoding="utf-8"))
        if pad is not None:
            data["records"] = [r for r in data["records"] if lo_lat - pad <= r["lat"] <= hi_lat + pad
                               and lo_lon - pad <= r["lon"] <= hi_lon + pad]
        (tmp_path / name).write_text(json.dumps(data), encoding="utf-8")
    return DataStore.from_dir(tmp_path)


@pytest.mark.skipif(not (SNAPSHOT / "MANIFEST.json").is_file() or not (SNAPSHOT / "routes").is_dir(),
                    reason="needs the downloaded snapshot with its route cache (#36, #92)")
@pytest.mark.parametrize("route_file", SNAPSHOT_ROUTES)
def test_indexed_equals_brute_force_on_snapshot_routes(tmp_path, route_file):
    path = SNAPSHOT / "routes" / route_file
    if not path.is_file():
        pytest.skip(f"{route_file} not in this snapshot's route cache")
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("_meta", None)
    route = Route.model_validate(payload)
    world = _snapshot_subset(tmp_path, route)
    expected = _assert_identical(world, route, max_candidates=10_000)  # every corridor station, not just 60
    assert len(expected) > 0
    cut = _assert_identical(world, route, max_candidates=60)  # and the production cut (#106: distributed)
    poly = [(p.lat, p.lon) for p in route.geometry]
    keep = select_distributed([c["along_route_km"] for c in expected], 60, polyline_km(poly),
                              power_kw=[max((p["power_kw"] or 0.0) for p in c["station"]["ports"]) for c in expected])
    assert cut == [expected[i] for i in keep]


def test_route_memo_returns_fresh_candidates_and_keys_on_geometry(store):
    fast = CorridorRetriever(store, 8.0, 400.0, 60)
    payload = json.loads(repo_path("data/fixtures/routes/phoenix__flagstaff.json").read_text(encoding="utf-8"))
    payload.pop("_meta", None)
    route = Route.model_validate(payload)
    first = fast.candidates(route, None)
    first[0].amenities.clear()  # a caller mutating its result must not leak into the next request
    first[0].offroute_km = -1.0
    again = fast.candidates(route, None)
    assert again[0] is not first[0] and again[0].offroute_km >= 0
    assert _dump(again) == _dump(_pair(store)[0].candidates(route, None))
    # same route_id, different geometry: not served from the memo
    shifted = route.model_copy(update={"geometry": [LatLon(lat=p.lat + 1.0, lon=p.lon) for p in route.geometry]})
    assert _dump(fast.candidates(shifted, None)) == _dump(_pair(store)[0].candidates(shifted, None))
