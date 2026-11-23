"""The indexed oracle path is a speed change only: identical results to the exhaustive path.

Every hand-built oracle test already runs both paths (TwinOracle in _oracle_world.py). These tests
add what those straight two-point equator routes cannot stress: bent multi-point routes at several
latitudes with stations crowding the buffer edge, several buffers (none too, for the corridor), the per-route
corridor itself (exact projections, not just the final answers), cache invalidation, and three
real snapshot routes (skipped when there is no snapshot).
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

import pytest
from _oracle_world import ENERGY, KM_PER_DEG, PROV, TwinOracle, itinerary, port, stop, vehicle

from clickless.contracts import LatLon, Place, Route, Station, TripRequest
from clickless.data.loaders import DataStore
from evaluation.oracle import _corridor_index as corridor_index
from evaluation.oracle import _geometry as geo
from evaluation.oracle._corridor_index import RouteIndex
from evaluation.oracle.label_setting import FileRouteSource, LabelSettingOracle, OracleConfig

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "data" / "snapshot"

# (name, lat, lon): Anchorage is where longitude cells are widest, Honolulu and the equator narrowest.
CENTERS = [("equator", 0.0, 10.0), ("phoenix", 33.4, -112.0), ("maine", 46.8, -68.5), ("anchorage", 61.2, -149.9)]


def _route(points: list[tuple[float, float]], km_per_geo_km: float = 1.2) -> Route:
    length = geo.path_length_km(points)
    return Route(
        route_id="bent",
        origin=LatLon(lat=points[0][0], lon=points[0][1]),
        destination=LatLon(lat=points[-1][0], lon=points[-1][1]),
        geometry=[LatLon(lat=a, lon=b) for a, b in points],
        distance_km=length * km_per_geo_km,
        duration_min=length * km_per_geo_km / 1.5,
        provenance=PROV,
    )


def _bent_points(rng: random.Random, lat0: float, lon0: float, n: int = 60) -> list[tuple[float, float]]:
    """A wandering route, ~300 km, with zig-zags, a repeated point and a near-backtrack."""
    pts, lat, lon = [], lat0, lon0
    heading = rng.uniform(0, 2 * math.pi)
    for k in range(n):
        pts.append((lat, lon))
        if k == n // 2:
            pts.append((lat, lon))  # repeated point: a zero-length segment
        heading += rng.uniform(-1.2, 1.2)
        step_km = rng.uniform(1, 9)
        lat += step_km * math.cos(heading) / KM_PER_DEG
        lon += step_km * math.sin(heading) / (KM_PER_DEG * math.cos(math.radians(lat)))
    return pts


def _stations_near(rng: random.Random, pts, buffer_km: float, n: int, prefix: str) -> list[Station]:
    """Stations scattered around the route, many within a hair of the buffer edge."""
    out = []
    for i in range(n):
        lat, lon = pts[rng.randrange(len(pts))]
        if i % 3 == 0:  # near the edge: offset from a vertex by buffer +- a few metres
            off = (buffer_km if math.isfinite(buffer_km) else 8.0) + rng.uniform(-0.01, 0.01)
        else:
            off = rng.uniform(0, 3 * (buffer_km if math.isfinite(buffer_km) else 8.0))
        ang = rng.uniform(0, 2 * math.pi)
        lat2 = lat + off * math.cos(ang) / KM_PER_DEG
        lon2 = lon + off * math.sin(ang) / (KM_PER_DEG * math.cos(math.radians(lat)))
        status = "available" if i % 11 else "planned"
        access = "public" if i % 13 else "restricted"
        kw = rng.choice([None, 50.0, 150.0, 350.0])
        out.append(
            Station(
                station_id=f"{prefix}{i:03d}",
                name=f"{prefix}{i}",
                lat=lat2,
                lon=lon2,
                access=access,
                status=status,
                ports=[port(kw=kw)] if i % 5 else [port("J1772", "l2", 7.2)],
                provenance=PROV,
            )
        )
    return out


# The Pareto search itself (unchanged by this work) is costly on dense random worlds; both paths
# get the same coarse SoC grid and label cap, so the comparison stays exact and the tests stay fast.
SEARCH = {"soc_grid_step_pct": 10.0, "max_labels": 3000}


def _twin(store: DataStore, route: Route, **cfg) -> TwinOracle:
    cfg = {**SEARCH, **cfg}

    def make(method):
        return LabelSettingOracle(
            store, OracleConfig(energy=ENERGY, method=method, **cfg), route_source=lambda o, d: route
        )

    return TwinOracle(make("indexed"), make("exhaustive"))


def _request(route: Route, start: float, vehicle_id: str = "t", **kw) -> TripRequest:
    o, d = route.origin, route.destination
    return TripRequest(
        request_id="r",
        raw_text="",
        origin=Place(text="a", lat=o.lat, lon=o.lon),
        destination=Place(text="b", lat=d.lat, lon=d.lon),
        vehicle_id=vehicle_id,
        start_soc_pct=start,
        **kw,
    )


def _exhaustive_corridor(stations, points, buffer_km):
    out = {}
    for st in stations:
        along, off = geo.locate((st.lat, st.lon), points)
        if off <= buffer_km + 1e-9:
            out[st.station_id] = (along, off)
    return out


@pytest.mark.parametrize("buffer_km", [2.0, 8.0, 25.0, math.inf])
@pytest.mark.parametrize("center", CENTERS, ids=lambda c: c[0])
def test_corridor_is_exact_on_bent_routes(center, buffer_km):
    name, lat0, lon0 = center
    rng = random.Random(f"{name}-{buffer_km}")
    pts = _bent_points(rng, lat0, lon0)
    stations = _stations_near(rng, pts, buffer_km, 400, "S")
    store = DataStore(stations, [vehicle()], [], {}, Path("."))
    index = RouteIndex()
    key = ("bent", tuple(pts))
    fast = index.corridor(store.stations, key, pts, buffer_km)
    slow = _exhaustive_corridor(stations, pts, buffer_km)
    assert fast == slow  # same stations, bit-identical (along, off)
    assert 0 < len(slow) < len(stations) or not math.isfinite(buffer_km)
    for st in stations:  # any station, in or out of the corridor
        assert index.locate(store.stations, key, pts, st) == geo.locate((st.lat, st.lon), pts)


@pytest.mark.parametrize("center", CENTERS, ids=lambda c: c[0])
def test_corridor_is_exact_with_fine_cells(center, monkeypatch):
    """With 0.1 deg cells a pad that is slightly too small rarely changes which cells a station
    falls in, so the test above cannot see it. ~110 m cells make the pad itself decide."""
    monkeypatch.setattr(corridor_index, "CELL_DEG", 0.001)
    name, lat0, lon0 = center
    rng = random.Random(f"fine-{name}")
    pts = _bent_points(rng, lat0, lon0, n=25)
    stations = _stations_near(rng, pts, 2.0, 600, "S")
    store = DataStore(stations, [vehicle()], [], {}, Path("."))
    assert RouteIndex().corridor(store.stations, ("fine",), pts, 2.0) == _exhaustive_corridor(stations, pts, 2.0)


@pytest.mark.parametrize("scope", ["stop", "total"])
@pytest.mark.parametrize("buffer_km", [3.0, 8.0])
@pytest.mark.parametrize("center", CENTERS, ids=lambda c: c[0])
def test_oracle_answers_identical_on_bent_routes(center, buffer_km, scope):
    name, lat0, lon0 = center
    rng = random.Random(f"answers-{name}-{buffer_km}-{scope}")
    pts = _bent_points(rng, lat0, lon0, n=40)
    route = _route(pts)
    stations = _stations_near(rng, pts, buffer_km, 40, "S")
    store = DataStore(stations, [vehicle(), vehicle("small", usable=25.0)], [], {}, Path("."))
    o = _twin(store, route, corridor_buffer_km=buffer_km, detour_scope=scope)
    for start, vid, kw in [(60, "t", {}), (35, "small", {"max_detour_min": 8}), (90, "t", {"arrival_soc_pct_min": 40})]:
        req = _request(route, start, vid, **kw)
        fr = o.is_feasible(req)  # TwinOracle asserts indexed == cached indexed == exhaustive
        front = o.pareto_plans(req)
        if front.complete:  # a capped search may stop before reaching the destination
            assert fr.feasible == bool(front.plans)
        # Check the front's plans and an itinerary through arbitrary (also off-corridor) stations.
        for plan in front.plans[:3]:
            o.check_itinerary(req, itinerary(*(stop(s.station_id, s.connector, s.port_power_kw) for s in plan.stops)))
        o.check_itinerary(req, itinerary(*(stop(st.station_id) for st in rng.sample(stations, 3))))


def test_with_overrides_shares_route_work_and_stays_identical():
    rng = random.Random("overrides")
    pts = _bent_points(rng, 40.0, -105.0)
    route = _route(pts)
    store = DataStore(_stations_near(rng, pts, 8.0, 40, "S"), [vehicle()], [], {}, Path("."))
    o = _twin(store, route)
    req = _request(route, 50)
    o.is_feasible(req)
    robust = o.with_overrides(efficiency_penalty=0.15, corridor_buffer_km=12.0)
    assert robust.fast._index is o.fast._index
    robust.is_feasible(req)
    robust.pareto_plans(req)


def test_caches_follow_a_changed_store():
    rng = random.Random("store-change")
    pts = _bent_points(rng, 33.4, -112.0)
    route = _route(pts)
    store = DataStore(_stations_near(rng, pts, 8.0, 40, "S"), [vehicle()], [], {}, Path("."))
    o = _twin(store, route)
    req = _request(route, 25)
    before = o.is_feasible(req)
    # Replace the stations with a different set: the index and the request cache must not serve stale work.
    new = _stations_near(random.Random("other"), pts, 8.0, 40, "N")
    store.stations = {s.station_id: s for s in new}
    after = o.is_feasible(req)
    assert set(after.witness) <= set(store.stations)
    assert after.excluded != before.excluded or after.witness != before.witness


def test_returned_results_are_not_the_cached_objects():
    o = _twin(DataStore([], [vehicle()], [], {}, Path(".")), _route([(0.0, 0.0), (0.0, 0.5)])).fast
    req = _request(_route([(0.0, 0.0), (0.0, 0.5)]), 5)  # start below reserve: rejected before searching
    first = o.is_feasible(req)
    first.excluded["mutated"] = 1
    first.detail = "mutated"
    assert o.is_feasible(req).detail != "mutated"
    assert "mutated" not in o.pareto_plans(req).feasibility.excluded


def test_method_is_validated():
    with pytest.raises(ValueError, match="method"):
        OracleConfig(energy=ENERGY, method="fast")


# --------------------------------------------------------------------------- real snapshot routes

SNAPSHOT_ROUTES = [
    ("san-jose-ca__palo-alto-ca.json", 15.0),
    ("mesa-az__glendale-az.json", 20.0),
    ("flagstaff-az__page-az.json", 45.0),
]


@pytest.fixture(scope="module")
def snapshot_store():
    if not (SNAPSHOT / "stations.json").exists():
        pytest.skip("no data/snapshot (download snapshot-v1-draft to run)")
    return DataStore.from_dir(SNAPSHOT)


@pytest.mark.parametrize("route_file,start", SNAPSHOT_ROUTES, ids=lambda v: str(v).split("__")[0])
def test_identical_on_real_snapshot_routes(snapshot_store, route_file, start):
    path = SNAPSHOT / "routes" / route_file
    if not path.exists():
        pytest.skip(f"no cached route {route_file}")
    route = Route.model_validate(json.loads(path.read_text(encoding="utf-8")))
    pts = [(p.lat, p.lon) for p in route.geometry]
    # The exhaustive path over all ~86k stations takes minutes per route, so compare on every
    # station within 0.25 deg of the route plus a random 500 elsewhere (the pruning must reject those).
    lats, lons = [p[0] for p in pts], [p[1] for p in pts]
    near = [
        s
        for s in snapshot_store.stations.values()
        if min(lats) - 0.25 <= s.lat <= max(lats) + 0.25 and min(lons) - 0.25 <= s.lon <= max(lons) + 0.25
    ]
    near_ids = {s.station_id for s in near}
    far = [s for s in snapshot_store.stations.values() if s.station_id not in near_ids]
    subset = near + random.Random(route_file).sample(far, 500)
    store = DataStore(subset, list(snapshot_store.vehicles.values()), [], {}, Path("."))

    fast = RouteIndex().corridor(store.stations, ("r",), pts, 8.0)
    assert fast == _exhaustive_corridor(subset, pts, 8.0)
    assert fast  # the corridor is not empty on these routes

    o = _twin(store, route, corridor_buffer_km=8.0)
    req = _request(route, start, "tesla-model-3-rwd-2024")
    o.is_feasible(req)
    front = o.pareto_plans(req)
    for plan in front.plans[:2]:
        o.check_itinerary(req, itinerary(*(stop(s.station_id, s.connector, s.port_power_kw) for s in plan.stops)))
    o.check_itinerary(req, itinerary(*(stop(s.station_id) for s in random.Random(1).sample(subset, 3))))


# --------------------------------------------------------------------------- the search itself, at 1 %

SEARCH_CASES = [
    # (route file, origin, destination, vehicle, start, max_labels): capped searches compare the
    # order-sensitive cut-off too, so the fast search must create and insert labels in the same order.
    ("denver-co__salt-lake-city-ut.json", "Denver, CO", "Salt Lake City, UT", "tesla-model-3-rwd-2024", 50, 2_000),
    ("denver-co__salt-lake-city-ut.json", "Denver, CO", "Salt Lake City, UT", "tesla-model-3-rwd-2024", 50, 8_000),
    ("flagstaff-az__page-az.json", "Flagstaff, AZ", "Page, AZ", "kia-ev9-lr-rwd-2024", 45, 200_000),
]


@pytest.mark.parametrize("case", SEARCH_CASES, ids=lambda c: f"{c[0].split('__')[0]}-{c[5]}")
def test_fast_search_identical_to_reference_on_real_routes_at_1_percent(snapshot_store, case):
    """The reference search (_pareto, used by method="exhaustive") and the fast one, on the same
    indexed problem, full snapshot store, 1 % SoC grid."""
    route_file, origin, dest, vehicle_id, start, max_labels = case
    if not (SNAPSHOT / "routes" / route_file).exists():
        pytest.skip(f"no cached route {route_file}")
    o = LabelSettingOracle(
        snapshot_store,
        OracleConfig(energy=ENERGY, soc_grid_step_pct=1.0, max_labels=max_labels, pareto_time_budget_s=None),
        route_source=FileRouteSource(SNAPSHOT / "routes"),
    )
    request = TripRequest(
        request_id="r",
        raw_text="",
        origin=snapshot_store.geocode(origin),
        destination=snapshot_store.geocode(dest),
        vehicle_id=vehicle_id,
        start_soc_pct=start,
    )
    fast, reference = o._pareto_fast(request), o._pareto(request)
    assert fast == reference
    assert fast.plans or not fast.complete
