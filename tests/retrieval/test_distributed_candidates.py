"""Route-distributed candidate cap (#106): the cap is spread along the route, not filled from the origin."""

import pytest

from clickless.contracts import (
    Amenity,
    AmenityCategory,
    AmenityPreference,
    ChargerLevel,
    ChargingPort,
    Connector,
    LatLon,
    Provenance,
    Route,
    StationStatus,
    TripRequest,
)
from clickless.data import DataStore
from clickless.planning.filters import DefaultHardConstraintFilter
from clickless.retrieval.corridor import (
    CorridorRetriever,
    polyline_km,
    required_preferences,
    satisfies_required,
    select_distributed,
    select_required_extras,
)

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
    return Route(
        route_id="r106",
        origin=geo[0],
        destination=geo[-1],
        geometry=geo,
        distance_km=1.0,
        duration_min=1.0,
        provenance=prov,
    )


def _world(store, n_origin=120):
    """A 3-degree north-south route: n_origin stations crowded at the origin, then one usable station
    every ~55 km (the issue's acceptance test: more than max_candidates near the origin)."""
    base = next(iter(store.stations.values()))
    stations = [
        base.model_copy(update={"station_id": f"origin-{k}", "lat": 33.0 + k * 1e-5, "lon": -112.0})
        for k in range(n_origin)
    ]
    stations += [
        base.model_copy(update={"station_id": f"later-{k}", "lat": 33.5 + k * 0.5, "lon": -112.0}) for k in range(6)
    ]
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
    dump = [
        [c.model_dump(mode="json") for c in CorridorRetriever(world, 8.0, 400.0, 60, method=m).candidates(route, None)]
        for m in ("indexed", "brute_force")
    ]
    assert dump[0] == dump[1]


def test_polyline_km_is_the_last_along_route_km(store):
    route = _route([(33.0, -112.0), (34.5, -112.0), (36.0, -112.0)])
    world = _world(store)
    cands = CorridorRetriever(world, 8.0, 400.0, 10_000).candidates(route, None)
    assert max(c.along_route_km for c in cands) <= polyline_km([(p.lat, p.lon) for p in route.geometry]) + 1e-9


# --------------------------------------------------------------------------- only usable stations compete (#133 review)

SPARK = "fixture-spark-lr-2025"  # CCS1 only, no adapters
UNUSABLE = {
    "wrong connector": {"ports": [ChargingPort(connector=Connector.NACS, level=ChargerLevel.DC_FAST, power_kw=350.0)]},
    "restricted": {"access": "restricted"},
    "unavailable": {"status": StationStatus.TEMPORARILY_UNAVAILABLE},
}


def test_an_unusable_entry_never_displaces_a_usable_one():
    # Hriday's boundary: 60 bins, each with a 350 kW and a 50 kW entry; only the 50 kW ones are usable
    along = [float(k * 10 + j) for k in range(60) for j in (0, 1)]
    power = [350.0, 50.0] * 60
    usable = [False, True] * 60
    keep = select_distributed(along, 60, 600.0, bin_km=10.0, power_kw=power, usable=usable)
    assert keep == [k * 2 + 1 for k in range(60)]  # every usable entry, no unusable one


def test_usable_entries_that_fit_are_all_kept_and_the_rest_fill_in_route_order():
    along = [0.0] * 70 + [200.0, 300.0]
    usable = [i % 10 == 0 for i in range(70)] + [True, True]  # 7 usable at the origin + 2 later
    keep = select_distributed(along, 60, 300.0, usable=usable)
    assert len(keep) == 60 and {i for i in range(72) if usable[i]} <= set(keep)
    assert [i for i in keep if not usable[i]] == [i for i in range(72) if not usable[i]][:51]


def test_the_old_prefix_is_kept_when_it_already_holds_every_usable_entry():
    along = [float(k) for k in range(100)]
    usable = [k < 30 for k in range(100)]
    assert select_distributed(along, 60, 100.0, usable=usable) == list(range(60))


def _bins_world(store, bad):
    """40 bins of 25 km on a 1,000 km north-south route; each bin holds a 350 kW station the
    Spark cannot use (`bad`) just before a 50 kW CCS1 station it can use."""
    base = next(iter(store.stations.values())).model_copy(
        update={
            "ports": [ChargingPort(connector=Connector.CCS1, level=ChargerLevel.DC_FAST, power_kw=50.0)],
            "status": StationStatus.AVAILABLE,
            "access": "public",
        }
    )
    stations = []
    for k in range(40):
        lat = 30.0 + k * 0.2248  # ~25 km per bin
        fast_ccs1 = base.model_copy(
            update={"ports": [ChargingPort(connector=Connector.CCS1, level=ChargerLevel.DC_FAST, power_kw=350.0)]}
        )
        stations.append(fast_ccs1.model_copy(update={"station_id": f"bad-{k}", "lat": lat, "lon": -100.0, **bad}))
        stations.append(base.model_copy(update={"station_id": f"good-{k}", "lat": lat + 0.001, "lon": -100.0}))
    world = DataStore(stations, list(store.vehicles.values()), [], dict(store.places), store.routes_dir)
    route = _route([(30.0, -100.0), (34.5, -100.0), (39.0, -100.0)])
    return world, route


@pytest.mark.parametrize("kind", sorted(UNUSABLE))
@pytest.mark.parametrize("method", ["indexed", "brute_force"])
def test_a_faster_unusable_station_never_pushes_out_a_slower_usable_one(store, kind, method):
    world, route = _bins_world(store, UNUSABLE[kind])
    hard = DefaultHardConstraintFilter()
    spark = world.vehicle(SPARK)

    def usable(station, request):
        return hard.station_rejection(station, request, spark) is None

    req = TripRequest(request_id="r", raw_text="t", vehicle_id=SPARK)
    # a 20-slot cap over 40 bins: one slot per bin can't hold both stations of a bin
    ids = {
        c.station.station_id
        for c in CorridorRetriever(world, 8.0, 400.0, 20, method=method, usable=usable).candidates(route, req)
    }
    assert len(ids) == 20 and all(i.startswith("good-") for i in ids)
    # with the production per-stretch bound the bad stations still never take a usable one's place
    bounded = CorridorRetriever(world, 8.0, 400.0, 20, method=method, usable=usable, per_bin=1).candidates(route, req)
    assert len(bounded) == 20 and all(c.station.station_id.startswith("good-") for c in bounded)
    # without the usable check, the 350 kW unusable stations would take the slots (the bug)
    blind = {
        c.station.station_id for c in CorridorRetriever(world, 8.0, 400.0, 20, method=method).candidates(route, req)
    }
    assert all(i.startswith("bad-") for i in blind)


def test_the_pipeline_passes_the_planner_station_checks_to_retrieval(pipe_a):
    station = next(iter(pipe_a.store.stations.values()))
    req = TripRequest(request_id="r", raw_text="t", vehicle_id=SPARK)
    restricted = station.model_copy(update={"access": "restricted"})
    assert pipe_a.retriever.usable is not None
    assert pipe_a.retriever.usable(restricted, req) is False
    assert pipe_a.retriever.usable(station, TripRequest(request_id="r", raw_text="t")) is True  # no vehicle


# --------------------------------------------------------------------------- per-stretch bound (#133 review)


def test_at_most_per_bin_usable_entries_per_stretch_fastest_first():
    along = [0.0, 1.0, 2.0, 3.0, 30.0, 31.0, 60.0]  # 3 stretches of 25 km on a 75 km route
    power = [50.0, 350.0, 150.0, 7.0, 50.0, 50.0, 11.0]
    keep = select_distributed(along, 60, 75.0, power_kw=power, usable=[True] * 7, per_bin=2)
    assert keep == [1, 2, 4, 5, 6]  # the 350 and 150 kW chargers at the origin; the other stretches fit


def test_per_bin_applies_even_under_the_cap_and_unusable_entries_still_fill_in():
    along = [0.0] * 10 + [5.0, 6.0]
    usable = [True] * 10 + [False, False]
    keep = select_distributed(along, 60, 20.0, usable=usable, per_bin=2)
    assert keep == [0, 1, 10, 11]  # 2 usable (ties: list order), then the unusable ones; others dropped


def test_per_bin_without_usable_bounds_every_entry():
    assert select_distributed([0.0] * 10, 60, 20.0, per_bin=3) == [0, 1, 2]


def test_production_config_bounds_usable_candidates_per_stretch(pipe_a):
    assert pipe_a.retriever.per_bin == 2


# --------------------------------------------------------------------- required amenities are retained (#133 / #136)


def test_required_extras_at_most_per_bin_per_stretch_closest_to_the_route_first():
    along = [0.0, 1.0, 2.0, 3.0, 4.0, 30.0]  # 2 stretches of 25 km on a 50 km route
    off = [0.1, 0.2, 3.0, 0.5, 1.0, 2.0]
    required = [True, False, True, True, True, True]
    usable = [True, True, True, True, False, True]
    extra = select_required_extras(along, off, [0, 1], 50.0, required, 2, usable=usable)
    # 0 is already kept, 4 is unusable; of 2 and 3 the closer one (3) ranks first, both fit under 2
    assert extra == [2, 3, 5]
    assert select_required_extras(along, off, [0, 1], 50.0, required, 1, usable=usable) == [3, 5]
    assert select_required_extras(along, off, [0, 1], 50.0, required, 0, usable=usable) == []


def test_only_important_wheelchair_preferences_are_required():
    """ADR 0003: only important + wheelchair_required is a hard requirement; everything else stays soft."""
    req = TripRequest(
        request_id="r",
        raw_text="t",
        amenities=[
            AmenityPreference(category=AmenityCategory.RESTROOM, wheelchair_required=True, priority="important"),
            AmenityPreference(category=AmenityCategory.RESTROOM, priority="important"),  # no wheelchair
            AmenityPreference(category=AmenityCategory.FOOD, priority="important"),
            AmenityPreference(category=AmenityCategory.RESTROOM, wheelchair_required=True),  # nice_to_have
            AmenityPreference(
                category=AmenityCategory.OTHER, phrase="ramp", wheelchair_required=True, priority="important"
            ),
        ],
    )
    assert required_preferences(req) == [(AmenityCategory.RESTROOM, True)]
    assert required_preferences(None) == []


def test_a_wheelchair_requirement_needs_an_explicit_yes_in_the_same_category():
    need = [(AmenityCategory.RESTROOM, True)]
    assert satisfies_required([_amenity("a", 0.0, wheelchair="yes")], need)
    assert not satisfies_required([_amenity("a", 0.0, wheelchair="unknown")], need)
    assert not satisfies_required([_amenity("a", 0.0, wheelchair="limited")], need)
    assert not satisfies_required([_amenity("a", 0.0, wheelchair="no")], need)
    assert not satisfies_required([_amenity("a", 0.0, category=AmenityCategory.FOOD, wheelchair="yes")], need)


def _amenity(amenity_id, lat, lon=-100.0, category=AmenityCategory.RESTROOM, wheelchair="unknown"):
    prov = Provenance(source="SYNTHETIC", retrieved_at="2026-11-21T00:00:00Z", status="cached")
    return Amenity(amenity_id=amenity_id, category=category, lat=lat, lon=lon, wheelchair=wheelchair, provenance=prov)


def _restroom_world(store, cafe_wheelchair="unknown"):
    """Scenario 4 in miniature: one 25 km stretch with two fast CCS1 chargers and two slow ones off
    the road. The slow dealer has a wheelchair=yes restroom; the slow cafe's is `cafe_wheelchair`."""
    base = next(iter(store.stations.values())).model_copy(
        update={"status": StationStatus.AVAILABLE, "access": "public"}
    )

    def station(sid, lat, lon, kw):
        port = ChargingPort(connector=Connector.CCS1, level=ChargerLevel.DC_FAST, power_kw=kw)
        return base.model_copy(update={"station_id": sid, "lat": lat, "lon": lon, "ports": [port]})

    stations = [
        station("fast-350", 30.01, -100.0, 350.0),
        station("fast-150", 30.02, -100.0, 150.0),
        station("dealer-50", 30.05, -100.02, 50.0),  # ~1.9 km off the route
        station("cafe-50", 30.06, -100.01, 50.0),  # ~1.0 km off the route
    ]
    amenities = [
        _amenity("osm:dealer-restroom", 30.0501, -100.02, wheelchair="yes"),
        _amenity("osm:cafe-restroom", 30.0601, -100.01, wheelchair=cafe_wheelchair),
    ]
    world = DataStore(stations, list(store.vehicles.values()), amenities, dict(store.places), store.routes_dir)
    return world, _route([(30.0, -100.0), (30.2, -100.0)])  # ~22 km: one stretch


def _restroom_request(priority, wheelchair=True):
    pref = AmenityPreference(category=AmenityCategory.RESTROOM, wheelchair_required=wheelchair, priority=priority)
    return TripRequest(request_id="r", raw_text="t", vehicle_id=SPARK, amenities=[pref])


def _dump(world, route, req, method, extra):
    retriever = CorridorRetriever(world, 8.0, 400.0, 60, method=method, per_bin=2, required_extra_per_bin=extra)
    return [c.model_dump(mode="json") for c in retriever.candidates(route, req)]


def _ids(world, route, req, method, extra):
    return [c["station"]["station_id"] for c in _dump(world, route, req, method, extra)]


@pytest.mark.parametrize("method", ["indexed", "brute_force"])
def test_important_wheelchair_restroom_is_retained_beyond_the_per_stretch_picks(store, method):
    world, route = _restroom_world(store)
    req = _restroom_request("important")
    assert _ids(world, route, req, method, None) == ["fast-350", "fast-150"]  # the bug: cut by power
    # only the dealer qualifies: the cafe's restroom is not tagged wheelchair=yes
    assert _ids(world, route, req, method, 2) == ["fast-350", "fast-150", "dealer-50"]


@pytest.mark.parametrize("method", ["indexed", "brute_force"])
def test_retention_is_bounded_per_stretch_closest_to_the_route_first(store, method):
    world, route = _restroom_world(store, cafe_wheelchair="yes")  # both slow stations qualify
    req = _restroom_request("important")
    assert _ids(world, route, req, method, 2) == ["fast-350", "fast-150", "dealer-50", "cafe-50"]
    assert _ids(world, route, req, method, 1) == ["fast-350", "fast-150", "cafe-50"]  # cafe is closer


@pytest.mark.parametrize("method", ["indexed", "brute_force"])
def test_important_without_wheelchair_leaves_candidates_unchanged(store, method):
    world, route = _restroom_world(store, cafe_wheelchair="yes")
    req = _restroom_request("important", wheelchair=False)
    assert _dump(world, route, req, method, 2) == _dump(world, route, req, method, None)
    food = TripRequest(
        request_id="r",
        raw_text="t",
        vehicle_id=SPARK,
        amenities=[AmenityPreference(category=AmenityCategory.FOOD, priority="important")],
    )
    assert _dump(world, route, food, method, 2) == _dump(world, route, food, method, None)


@pytest.mark.parametrize("method", ["indexed", "brute_force"])
def test_nice_to_have_wheelchair_leaves_candidates_unchanged(store, method):
    world, route = _restroom_world(store, cafe_wheelchair="yes")
    req = _restroom_request("nice_to_have")
    assert _dump(world, route, req, method, 2) == _dump(world, route, req, method, None)


@pytest.mark.parametrize("method", ["indexed", "brute_force"])
def test_requests_without_amenities_get_exactly_the_same_candidates(store, method):
    world, route = _restroom_world(store, cafe_wheelchair="yes")
    for req in (TripRequest(request_id="r", raw_text="t", vehicle_id=SPARK), None):
        assert _dump(world, route, req, method, 2) == _dump(world, route, req, method, None)


@pytest.mark.parametrize("cafe_wheelchair", ["unknown", "yes"])
@pytest.mark.parametrize("extra", [1, 2])
def test_both_methods_agree_on_the_required_extras(store, cafe_wheelchair, extra):
    world, route = _restroom_world(store, cafe_wheelchair=cafe_wheelchair)
    req = _restroom_request("important")
    assert _dump(world, route, req, "indexed", extra) == _dump(world, route, req, "brute_force", extra)


def test_production_config_retains_up_to_two_required_stations_per_stretch(pipe_a):
    assert pipe_a.retriever.required_extra_per_bin == 2
