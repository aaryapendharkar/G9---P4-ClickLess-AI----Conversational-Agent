"""Required accessible restroom in the oracle (ADR 0003 question 3, #162).

Rule: a restroom preference with priority "important" AND wheelchair_required makes a plan valid
only if one charging stop has a restroom tagged wheelchair=yes within amenity_radius_m (400 m)
straight-line. "unknown" and "limited" never count; nice_to_have + wheelchair stays soft.

Hand-built world (tests/evaluation/_oracle_world.py): route of 200 km along the equator, vehicle T
uses 1 % SoC per 2.5 km. From 50 % the car reaches 100 km at 10 % (the reserve), so every plan
needs exactly one stop before the destination. Each query runs on the indexed oracle and the
exhaustive reference (TwinOracle), which must agree.
"""

from __future__ import annotations

import json
from pathlib import Path

import _access_scenarios as scenarios
import pytest
from _access_scenarios import amenity
from _oracle_world import ENERGY, TwinOracle, from_plan, itinerary, port, req, route, station, stop, vehicle

from clickless.config import load_config
from clickless.contracts import AmenityPreference
from clickless.data.loaders import DataStore
from evaluation.oracle.label_setting import (
    LabelSettingOracle,
    OracleConfig,
    requires_accessible_restroom,
)


def access_world(stations, amenities, **cfg) -> TwinOracle:
    r = route(200.0)
    store = DataStore(list(stations), [vehicle()], list(amenities), {}, Path("."))

    def make(method):
        return LabelSettingOracle(store, OracleConfig(energy=ENERGY, method=method, **cfg), route_source=lambda o, d: r)

    return TwinOracle(make("indexed"), make("exhaustive"))


def restroom(priority="important", wheelchair=True) -> AmenityPreference:
    return AmenityPreference(category="restroom", wheelchair_required=wheelchair, priority=priority)


REQUIRED = [restroom()]
# s60 (no qualifying restroom, 150 kW) and s90 (where the accessible restroom goes, 50 kW): without
# the requirement the faster-charging s60 plan dominates the s90 one.
STATIONS = (station("s60", 60), station("s90", 90, ports=[port(kw=50.0)]))


def access_violations(report):
    return [v for v in report.violations if v.code == "access" and "wheelchair" in v.detail]


# ---------------------------------------------------------------- which requests the rule applies to


@pytest.mark.parametrize(
    ("prefs", "required"),
    [
        ([restroom()], True),
        ([restroom("nice_to_have")], False),
        ([restroom(wheelchair=False)], False),
        ([AmenityPreference(category="food", wheelchair_required=True, priority="important")], False),
        ([restroom("nice_to_have"), AmenityPreference(category="coffee", priority="important")], False),
        ([AmenityPreference(category="coffee"), restroom()], True),
        ([], False),
    ],
)
def test_requires_accessible_restroom(prefs, required):
    assert requires_accessible_restroom(req(amenities=prefs)) is required


# ---------------------------------------------------------------- is_feasible / pareto_plans


def test_required_and_satisfied_is_feasible_through_the_qualifying_stop():
    o = access_world(STATIONS, [amenity("r90", 90, 200, wheelchair="yes")])
    r = req(amenities=REQUIRED)
    fr = o.is_feasible(r)
    assert fr.feasible and "s90" in fr.witness
    front = o.pareto_plans(r)
    assert front.complete and front.plans
    assert all("s90" in p.station_ids for p in front.plans)
    # without the requirement the faster-to-reach s60 is also a valid plan
    soft = o.pareto_plans(req(amenities=[restroom("nice_to_have")]))
    assert any(p.station_ids == ("s60",) for p in soft.plans)


@pytest.mark.parametrize("tag", ["limited", "unknown", "no"])
def test_only_limited_or_unknown_restrooms_is_infeasible(tag):
    o = access_world(STATIONS, [amenity("r60", 60, 50, wheelchair=tag), amenity("r90", 90, 50, wheelchair=tag)])
    fr = o.is_feasible(req(amenities=REQUIRED))
    assert not fr.feasible and fr.evaluable
    assert fr.reason == "no_accessible_restroom"
    assert o.pareto_plans(req(amenities=REQUIRED)).plans == []


def test_a_qualifying_restroom_outside_the_radius_does_not_count():
    # 401 m from s90 (station on the route, restroom 401 m north): just outside 400 m
    o = access_world(STATIONS, [amenity("r90", 90, 401, wheelchair="yes")])
    assert o.is_feasible(req(amenities=REQUIRED)).reason == "no_accessible_restroom"
    # the radius comes from the config: a wider one makes it count
    wide = o.with_overrides(amenity_radius_m=450.0)
    assert wide.is_feasible(req(amenities=REQUIRED)).feasible


def test_a_wheelchair_accessible_amenity_of_another_category_does_not_count():
    o = access_world(STATIONS, [amenity("f90", 90, 50, category="food", wheelchair="yes")])
    assert o.is_feasible(req(amenities=REQUIRED)).reason == "no_accessible_restroom"


def test_nice_to_have_wheelchair_restroom_leaves_the_rule_off():
    o = access_world(STATIONS, [amenity("r60", 60, 50, wheelchair="limited")])
    soft = req(amenities=[restroom("nice_to_have")])
    assert o.is_feasible(soft).feasible
    assert o.pareto_plans(soft).plans
    for p in o.pareto_plans(soft).plans:
        assert o.check_itinerary(soft, from_plan(p)).ok


def test_no_stop_trip_does_not_satisfy_the_requirement():
    # From 95 % the 200 km drive is direct (arrival 15 % >= reserve, arrival_soc_pct_min 10).
    o = access_world(STATIONS, [amenity("r90", 90, 50, wheelchair="yes")])
    direct_soft = req(start=95.0, arrival_soc_pct_min=10)
    assert o.is_feasible(direct_soft).witness == []
    required = req(start=95.0, arrival_soc_pct_min=10, amenities=REQUIRED)
    fr = o.is_feasible(required)
    assert fr.feasible and "s90" in fr.witness  # it must stop, even though it doesn't need to charge
    # The car arrives at s90 with more than it needs, so the front has a zero-charge restroom stop
    # (found on deepti-022: feasible but an empty front before targets allowed departing as arrived).
    plans = o.pareto_plans(required).plans
    assert plans and all(p.stops for p in plans)
    assert any(p.station_ids == ("s90",) and p.total_charge_min == 0.0 for p in plans)
    for p in plans:
        assert o.check_itinerary(required, from_plan(p)).ok
    rep = o.check_itinerary(required, itinerary())
    assert not rep.ok and access_violations(rep)

    # With no qualifying restroom anywhere, a drive that needs no charge is still infeasible.
    none = access_world(STATIONS, [])
    assert none.is_feasible(required).reason == "no_accessible_restroom"


def test_energy_reasons_still_win_when_the_trip_is_infeasible_anyway():
    o = access_world((station("s150", 150),), [amenity("r150", 150, 50, wheelchair="yes")])
    fr = o.is_feasible(req(amenities=REQUIRED))  # s150 is beyond the 100 km reach
    assert fr.reason == "energy_gap"


def test_requirement_with_a_total_detour_limit():
    # s90 is 1 km off-route (3.9 + 2 = 5.9 min detour); s60 on the route (2 min).
    o = access_world(
        (station("s60", 60), station("s90", 90, 1.0)),
        [amenity("r90", 90, 1000, wheelchair="yes")],
        detour_scope="total",
    )
    assert o.is_feasible(req(amenities=REQUIRED, max_detour_min=6.0)).feasible
    fr = o.is_feasible(req(amenities=REQUIRED, max_detour_min=5.0))
    assert fr.reason == "no_accessible_restroom"  # s60 alone is within 5 min, s90 is not


def test_the_satisfying_stop_need_not_be_the_first():
    # Two stops: s30 (no restroom) then s90 (accessible). From 25 % the car reaches 37.5 km.
    o = access_world((station("s30", 30), station("s90", 90)), [amenity("r90", 90, 50, wheelchair="yes")])
    r = req(start=25.0, amenities=REQUIRED)
    assert o.is_feasible(r).witness == ["s30", "s90"]
    plans = o.pareto_plans(r).plans
    assert plans and all(p.station_ids[-1] == "s90" for p in plans)


def test_feasible_iff_the_front_is_non_empty_and_every_front_plan_passes():
    worlds = [
        access_world(STATIONS, [amenity("r90", 90, 50, wheelchair="yes")]),
        access_world(STATIONS, [amenity("r60", 60, 50, wheelchair="limited")]),
        access_world((station("s30", 30), *STATIONS), [amenity("r30", 30, 50, wheelchair="yes")]),
    ]
    for o in worlds:
        for scope in ("stop", "total"):
            oo = o.with_overrides(detour_scope=scope)
            for start, arrival in ((25.0, None), (50.0, None), (95.0, None), (95.0, 10.0), (99.0, 10.0)):
                r = req(start=start, amenities=REQUIRED, max_detour_min=10.0, arrival_soc_pct_min=arrival)
                front = oo.pareto_plans(r)
                assert front.complete
                assert bool(oo.is_feasible(r)) == bool(front.plans), (start, arrival, scope)
                for p in front.plans:
                    assert oo.check_itinerary(r, from_plan(p)).ok


# ---------------------------------------------------------------- check_itinerary


def test_check_itinerary_flags_a_plan_without_the_accessible_restroom():
    o = access_world(STATIONS, [amenity("r90", 90, 50, wheelchair="yes"), amenity("r60", 60, 50, wheelchair="limited")])
    r = req(amenities=REQUIRED)
    without = itinerary(stop("s60", kw=150.0, km=60, arrive=26.0, depart=80.0))
    rep = o.check_itinerary(r, without)
    assert not rep.ok and not rep.strict_ok
    [v] = access_violations(rep)
    assert v.code == "access" and "400 m" in v.detail and v.stop_index is None
    assert [x.code for x in rep.violations] == ["access"]  # energy etc. are fine
    # the same plan is valid when the restroom is only nice to have
    assert o.check_itinerary(req(amenities=[restroom("nice_to_have")]), without).ok


def test_check_itinerary_accepts_a_plan_through_the_accessible_restroom():
    o = access_world(STATIONS, [amenity("r90", 90, 50, wheelchair="yes")])
    rep = o.check_itinerary(req(amenities=REQUIRED), itinerary(stop("s90", km=90, arrive=14.0, depart=80.0)))
    assert rep.ok, rep.violations


def test_check_itinerary_any_stop_can_satisfy_it():
    o = access_world((station("s30", 30), station("s90", 90)), [amenity("r30", 30, 50, wheelchair="yes")])
    it = itinerary(
        stop("s30", kw=150.0, km=30, arrive=38.0, depart=60.0),
        stop("s90", kw=150.0, km=90, arrive=36.0, depart=80.0),
    )
    rep = o.check_itinerary(req(amenities=REQUIRED), it)
    assert rep.ok, rep.violations


# ---------------------------------------------------------------- unchanged without the requirement


def test_requests_without_the_requirement_give_the_recorded_pre_162_answers():
    """Golden answers recorded on main before the rule (4e739b5), on worlds that DO carry
    accessible, limited and unknown restrooms: is_feasible, pareto_plans and check_itinerary
    (front plans + hand-built plans), both detour scopes, indexed and exhaustive."""
    golden = json.loads(scenarios.GOLDEN.read_text(encoding="utf-8"))
    now = scenarios.run_all()
    assert sorted(now) == sorted(golden)
    for name in golden:
        assert now[name] == golden[name], name


def test_amenities_change_nothing_without_the_requirement():
    """Same requests on the same world with and without amenities in the store."""
    with_amenities = scenarios.oracle()
    bare = access_world(scenarios.STATIONS, [])
    for kw in scenarios.REQUESTS.values():
        kw = dict(kw)
        r = req(start=kw.pop("start", 50.0), **kw)
        assert not requires_accessible_restroom(r)
        assert with_amenities.is_feasible(r) == bare.is_feasible(r)
        a, b = with_amenities.pareto_plans(r), bare.pareto_plans(r)
        assert (a.plans, a.complete, a.n_labels) == (b.plans, b.complete, b.n_labels)


# ---------------------------------------------------------------- configuration


def test_radius_comes_from_the_retrieval_config():
    cfg = load_config("A")
    o = LabelSettingOracle.from_config(cfg, DataStore([], [vehicle()], [], {}, Path(".")))
    assert o.cfg.amenity_radius_m == float(cfg["retrieval"]["amenity_radius_m"]) == 400.0
