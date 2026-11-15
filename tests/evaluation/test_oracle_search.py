"""Feasibility and Pareto search of the oracle on hand-built worlds with known answers.

Route 200 km / 120 min unless stated; vehicle T: 1 % SoC = 2.5 km, so the whole route costs 80 %.
"""

import pytest
from _oracle_world import from_plan, off_for_detour, port, req, station, world

SCOPES = ["stop", "total"]


# --------------------------------------------------------------------------- feasibility rules


def test_direct_trip_uses_default_arrival_when_unstated():
    o = world()
    fr = o.is_feasible(req(start=100))  # arrives with exactly 20 % = default arrival
    assert fr.feasible and fr.witness == []
    plans = o.pareto_plans(req(start=100)).plans
    assert len(plans) == 1 and plans[0].stops == () and plans[0].total_time_min == pytest.approx(120.0)


def test_stated_arrival_above_reach():
    fr = world().is_feasible(req(start=100, arrival_soc_pct_min=25))
    assert (fr.feasible, fr.reason) == (False, "arrival_soc_unreachable")


def test_reserve_boundary_at_a_station():
    o = world([station("S", 150)])
    assert o.is_feasible(req(start=70)).feasible  # arrives at S with exactly the 10 % reserve
    fr = o.is_feasible(req(start=69.9))
    assert (fr.feasible, fr.reason) == (False, "energy_gap")


def test_start_below_reserve():
    assert world([station("S", 10)]).is_feasible(req(start=5)).reason == "start_below_reserve"


def test_override_without_overlap_is_infeasible():
    o = world([station("X", 60, ports=[port("CHADEMO")])])
    fr = o.is_feasible(req(connectors_override=["CHADEMO"]))
    assert (fr.feasible, fr.reason) == (False, "connector_no_overlap")


def test_unknown_vehicle_is_infeasible_and_missing_fields_not_evaluable():
    o = world()
    assert o.is_feasible(req(vehicle_id="nope")).reason == "unknown_vehicle"
    fr = o.is_feasible(req().model_copy(update={"start_soc_pct": None}))
    assert not fr.evaluable and fr.reason == "missing_fields"


@pytest.mark.parametrize(
    "kwargs,request_kw,reason",
    [
        ({"status": "temporarily_unavailable"}, {}, "status"),
        ({"status": "planned"}, {}, "status"),
        ({"access": "restricted"}, {}, "access"),
        ({"ports": [port("CHADEMO")]}, {}, "connector"),
        ({"ports": [port("NACS", "l2", 11)]}, {}, "connector"),  # adapter at AC
        ({"ports": [port(kw=None)]}, {"min_power_kw": 50}, "power"),  # unknown power fails min_power
        ({"ports": [port(kw=0.0)]}, {}, "power"),
        ({"ports": [port(kw=100)]}, {"min_power_kw": 150}, "power"),
        ({"off_km": 9.0}, {}, "buffer"),
        ({"off_km": off_for_detour(6)}, {"max_detour_min": 5}, "detour"),
    ],
)
def test_excluded_station_makes_trip_infeasible(kwargs, request_kw, reason):
    # Start 50 % cannot drive 200 km (80 %); the only station is at km 60.
    assert world([station("S", 60)]).is_feasible(req(**request_kw)).feasible  # baseline: usable station works
    fr = world([station("S", 60, **kwargs)]).is_feasible(req(**request_kw))
    assert (fr.feasible, fr.reason, fr.excluded) == (False, "no_usable_station", {reason: 1})


def test_unknown_power_without_min_power_stays_a_candidate():
    fr = world([station("S", 60, ports=[port(kw=None)])]).is_feasible(req())
    assert fr.feasible and fr.witness == ["S"]


def test_station_outside_buffer_is_feasible_without_buffer():
    far = station("FAR", 60, off_km=9.0)
    assert not world([far]).is_feasible(req()).feasible
    assert world([far], corridor_buffer_km=float("inf")).is_feasible(req()).feasible


def test_unknown_status_station_is_used_and_reported():
    o = world([station("U", 60, status="unknown")])
    fr = o.is_feasible(req())
    assert fr.feasible and fr.unknown_status_station_ids == ["U"]
    assert [p.unknown_status_station_ids for p in o.pareto_plans(req()).plans] == [("U",)]


def test_leaf_flag_on_feasibility_result():
    from _oracle_world import vehicle

    leaf = vehicle("nissan-leaf-sv-plus-2024", native=("CHADEMO", "J1772"), adapters=())
    o = world([station("C", 60, ports=[port("CHADEMO", kw=50)])], vehicles=[leaf])
    fr = o.is_feasible(req(vehicle_id="nissan-leaf-sv-plus-2024"))
    assert fr.feasible and fr.charge_time_excluded


# --------------------------------------------------------------------------- Pareto front


def test_pareto_front_fast_far_vs_slow_on_route():
    # F: 150 kW (80 kW for the car), 6 min detour.  S: 50 kW on the route (2 min).  D: like S but 6 min.
    stations = [
        station("F", 60, off_for_detour(6), [port(kw=150)]),
        station("S", 60, 0.0, [port(kw=50)]),
        station("D", 60, off_for_detour(6), [port(kw=50)]),
    ]
    plans = world(stations).pareto_plans(req())
    assert plans.complete
    by_ids = {p.station_ids: p for p in plans.plans}
    assert set(by_ids) == {("F",), ("S",)}  # D is dominated by S, two-stop plans by one of them
    s = by_ids[("S",)]  # arrive 26 %, charge to 76 % (56 % + 20 %): 25 kWh / 50 kW = 30 min + 3
    assert s.objectives == pytest.approx((155.0, 2.0, 33.0))
    assert s.stops[0].arrive_soc_pct == pytest.approx(26.0) and s.stops[0].depart_soc_pct == pytest.approx(76.0)
    f = by_ids[("F",)]  # access 1.333 km each way: arrive 25.467 %, depart 76.533 %: 25.533 kWh / 80 kW
    assert f.objectives == pytest.approx((148.15, 6.0, 22.15), abs=1e-3)


def test_pareto_stops_at_cheaper_station_when_charging_more_is_faster_elsewhere():
    # A slow on-route station early and a fast one later: charging just enough to reach the fast one wins on time.
    stations = [station("SLOW", 40, 0.0, [port(kw=20)]), station("FAST", 100, 0.0, [port(kw=150)])]
    plans = world(stations).pareto_plans(req(start=30))
    fastest = min(plans.plans, key=lambda p: p.total_time_min)
    assert fastest.station_ids == ("SLOW", "FAST")
    assert fastest.stops[0].depart_soc_pct == pytest.approx(10 + 60 * 0.4)  # just enough to reach FAST with reserve


# --------------------------------------------------------------------------- detour scope


def _scope_world_a(scope):
    # Route 300 km. A (km 90) and B (km 180) are both needed and each costs 6 min of detour.
    return world(
        [station("A", 90, off_for_detour(6)), station("B", 180, off_for_detour(6))], route_km=300, detour_scope=scope
    )


def _scope_world_b(scope):
    # As above plus A2 (km 85, on route, 2 min). Max reach goes through A (A+B = 12 min);
    # the feasible path under a 10-min trip total is A2+B (8 min).
    return world(
        [station("A", 90, off_for_detour(6)), station("A2", 85), station("B", 180, off_for_detour(6))],
        route_km=300,
        detour_scope=scope,
    )


def test_feasible_per_stop_but_not_by_total_detour():
    r = req(route_km=300, max_detour_min=10)
    assert _scope_world_a("stop").is_feasible(r).witness == ["A", "B"]
    fr = _scope_world_a("total").is_feasible(r)
    assert (fr.feasible, fr.reason) == (False, "detour_total_exceeded")
    assert _scope_world_a("total").pareto_plans(r).plans == []


def test_total_scope_keeps_lower_detour_path_alive():
    r = req(route_km=300, max_detour_min=10)
    fr = _scope_world_b("total").is_feasible(r)
    assert fr.feasible and fr.witness == ["A2", "B"]
    plans = _scope_world_b("total").pareto_plans(r).plans
    assert plans and all(p.total_detour_min <= 10 + 1e-9 for p in plans)
    assert {p.station_ids for p in plans} == {("A2", "B")}


# --------------------------------------------------------------------------- invariant on every case

CASES = {
    "direct": (lambda s: world(detour_scope=s), req(start=100)),
    "direct_short": (lambda s: world(detour_scope=s), req(start=99)),
    "reserve_exact": (lambda s: world([station("S", 150)], detour_scope=s), req(start=70)),
    "reserve_miss": (lambda s: world([station("S", 150)], detour_scope=s), req(start=69.9)),
    "pareto": (
        lambda s: world([station("F", 60, off_for_detour(6)), station("S", 60, 0.0, [port(kw=50)])], detour_scope=s),
        req(max_detour_min=8),
    ),
    "no_overlap": (lambda s: world([station("S", 60)], detour_scope=s), req(connectors_override=["CHADEMO"])),
    "buffer": (lambda s: world([station("FAR", 60, 9.0)], detour_scope=s), req()),
    "adapter_dc": (lambda s: world([station("N", 60, ports=[port("NACS", kw=250)])], detour_scope=s), req()),
    "adapter_ac": (lambda s: world([station("N", 60, ports=[port("NACS", "l2", 11)])], detour_scope=s), req()),
    "scope_a": (_scope_world_a, req(route_km=300, max_detour_min=10)),
    "scope_b": (_scope_world_b, req(route_km=300, max_detour_min=10)),
    "scope_b_tight": (_scope_world_b, req(route_km=300, max_detour_min=7)),
    "robust": (lambda s: world([station("S", 60)], detour_scope=s, efficiency_penalty=0.15), req()),
}


@pytest.mark.parametrize("scope", SCOPES)
@pytest.mark.parametrize("case", sorted(CASES))
def test_feasible_iff_front_non_empty_and_every_front_plan_passes_the_check(case, scope):
    build, r = CASES[case]
    o = build(scope)
    fr, front = o.is_feasible(r), o.pareto_plans(r)
    assert front.complete
    assert fr.feasible == bool(front.plans), (case, scope, fr.reason)
    for plan in front.plans:  # two independent code paths inside the oracle must agree
        report = o.check_itinerary(r, from_plan(plan))
        assert report.ok, (case, scope, plan.station_ids, [v.detail for v in report.violations])
        assert report.objectives == pytest.approx(plan.objectives, abs=1e-6)
