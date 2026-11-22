"""Route-distributed candidate cap (#106): the cap is spread along the route, not filled from the origin."""

import pytest

from clickless.contracts import LatLon, Provenance, Route
from clickless.data import DataStore
from clickless.retrieval.corridor import CorridorRetriever, polyline_km, select_distributed

# --------------------------------------------------------------------------- select_distributed


def test_everything_is_kept_under_the_cap():
    assert select_distributed([0.0, 0.0, 5.0], 3, 100.0) == [0, 1, 2]
    assert select_distributed([], 60, 100.0) == []


def test_a_prefix_that_already_spans_the_route_is_kept():
    along = [float(k) for k in range(100)]  # one station per km on a 100 km route, 4 bins of 25 km
    # the first 60 stations reach km 59 only, so bin 3 (75-100 km) is not reached: redistributed
    keep = select_distributed(along, 60, 100.0)
    assert keep != list(range(60)) and any(along[i] >= 75 for i in keep)
    # on a 50 km route (2 bins) the first 60 reach both bins: unchanged
    assert select_distributed([k * 0.5 for k in range(100)], 60, 50.0) == list(range(60))


def test_a_route_shorter_than_one_bin_keeps_the_prefix():
    dense = [0.0] * 500 + [10.0] * 5  # a dense origin on a 20 km urban route: one bin
    assert select_distributed(dense, 60, 20.0) == list(range(60))


def test_a_dense_origin_no_longer_takes_the_whole_budget():
    along = [0.0] * 500 + [100.0, 200.0, 300.0, 400.0]  # 400 km, 16 bins of 25 km
    keep = select_distributed(along, 60, 400.0)
    assert len(keep) == 60 and keep == sorted(keep)
    assert {500, 501, 502, 503} <= set(keep)  # every later station survives
    assert sum(1 for i in keep if along[i] == 0.0) == 56  # the origin keeps the rest of the budget


def test_round_robin_shares_the_budget_across_bins():
    along = [0.0] * 100 + [60.0] * 100 + [110.0] * 100  # 3 crowded bins on a 125 km route (5 bins)
    keep = select_distributed(along, 30, 125.0)
    assert [sum(1 for i in keep if along[i] == x) for x in (0.0, 60.0, 110.0)] == [10, 10, 10]


def test_within_a_bin_the_fastest_stations_go_first():
    along = [0.0, 0.1, 0.2, 0.3, 100.0]
    power = [7.0, 350.0, 11.0, 150.0, 50.0]  # a city bin of Level 2 posts and two DC fast chargers
    keep = select_distributed(along, 3, 125.0, power_kw=power)
    assert keep == [1, 3, 4]  # the 350 kW and 150 kW chargers, plus the later station


def test_selection_is_deterministic_and_ties_fall_back_to_list_order():
    along = [0.0] * 10 + [80.0] * 10
    power = [50.0] * 20
    first = select_distributed(along, 4, 100.0, power_kw=power)
    assert first == select_distributed(along, 4, 100.0, power_kw=power) == [0, 1, 10, 11]


def test_never_more_than_the_cap():
    for cap in (1, 2, 7, 60):
        along = [k * 0.37 for k in range(1000)]
        assert len(select_distributed(along, cap, 370.0)) == cap


# --------------------------------------------------------------------------- retriever (both methods)


def _route(points):
    geo = [LatLon(lat=a, lon=b) for a, b in points]
    prov = Provenance(source="SYNTHETIC", retrieved_at="2026-11-21T00:00:00Z", status="cached")
    return Route(route_id="r106", origin=geo[0], destination=geo[-1], geometry=geo, distance_km=1.0,
                 duration_min=1.0, provenance=prov)


def _world(store, n_origin=120):
    """A 3-degree north-south route: n_origin stations crowded at the origin, then one usable station
    every ~55 km (the issue's acceptance test: more than max_candidates near the origin)."""
    base = next(iter(store.stations.values()))
    stations = [base.model_copy(update={"station_id": f"origin-{k}", "lat": 33.0 + k * 1e-5, "lon": -112.0})
                for k in range(n_origin)]
    stations += [base.model_copy(update={"station_id": f"later-{k}", "lat": 33.5 + k * 0.5, "lon": -112.0})
                 for k in range(6)]
    return DataStore(stations, list(store.vehicles.values()), [], dict(store.places), store.routes_dir)


@pytest.mark.parametrize("method", ["indexed", "brute_force"])
def test_later_route_stations_survive_a_dense_origin(store, method):
    world = _world(store)
    route = _route([(33.0, -112.0), (34.5, -112.0), (36.0, -112.0)])
    cands = CorridorRetriever(world, 8.0, 400.0, 60, method=method).candidates(route, None)
    ids = [c.station.station_id for c in cands]
    assert len(ids) == 60
    assert {f"later-{k}" for k in range(6)} <= set(ids)  # the charging chain is still there
    assert [c.along_route_km for c in cands] == sorted(c.along_route_km for c in cands)


def test_both_methods_agree_on_the_distributed_cut(store):
    world = _world(store)
    route = _route([(33.0, -112.0), (34.5, -112.0), (36.0, -112.0)])
    dump = [[c.model_dump(mode="json") for c in CorridorRetriever(world, 8.0, 400.0, 60, method=m).candidates(
        route, None)] for m in ("indexed", "brute_force")]
    assert dump[0] == dump[1]


def test_polyline_km_is_the_last_along_route_km(store):
    route = _route([(33.0, -112.0), (34.5, -112.0), (36.0, -112.0)])
    world = _world(store)
    cands = CorridorRetriever(world, 8.0, 400.0, 10_000).candidates(route, None)
    assert max(c.along_route_km for c in cands) <= polyline_km([(p.lat, p.lon) for p in route.geometry]) + 1e-9
