"""Planning metrics on hand-built items and results with exact expected rates."""

import dataclasses

import pytest
from _oracle_world import itinerary, off_for_detour, port, result, station, stop, vehicle, world

from clickless.contracts import VerificationReport
from evaluation.metrics.planning import (
    corridor_buffer_diagnostic,
    feasibility_and_violations,
    infeasible_handling,
    planning_metrics,
    request_from_item,
    robust_feasibility,
    unknown_power_sensitivity,
    unknown_status_reliance,
)
from evaluation.schema import BenchmarkItem

LEAF = "nissan-leaf-sv-plus-2024"


def item(item_id, **gold):
    g = {"origin": "a", "destination": "b", "vehicle_id": "t", "start_soc_pct": 50, **gold}
    return BenchmarkItem(
        id=item_id,
        author="ian",
        category="rural",
        route_type="rural",
        text=item_id,
        gold_request=g,
        clarification_required=False,
    )


def oracle():
    return world([station("S", 60, ports=[port(kw=50)])])


ITEMS = [
    item("i1"),  # feasible, valid plan, survives +15 %
    item("i2"),  # feasible, plan misses the arrival SoC
    item("i3", connectors_override=["CHADEMO"]),  # infeasible, correctly rejected
    item("i4", connectors_override=["CHADEMO"]),  # infeasible, bogus plan returned
    item("i5"),  # feasible, valid nominally, fails at +15 %
    BenchmarkItem(
        id="i6",
        author="ian",
        category="incomplete",
        route_type="rural",
        text="i6",
        gold_request={"origin": "a"},
        clarification_required=True,
    ),  # not evaluable
]
RESULTS = [
    result("i1", itins=[itinerary(stop("S", depart=90))]),
    result("i2", itins=[itinerary(stop("S", depart=75))]),
    result("i3", status="infeasible"),
    result("i4", itins=[itinerary(stop("S", depart=90))]),
    result("i5", itins=[itinerary(stop("S", depart=80))]),
    result("i6", status="rejected_incomplete"),
]


def test_request_from_item():
    assert request_from_item(ITEMS[-1]) is None
    r = request_from_item(ITEMS[2])
    assert r.origin.text == "a" and [c.value for c in r.connectors_override] == ["CHADEMO"]


def test_feasibility_and_violations():
    m = feasibility_and_violations(ITEMS, RESULTS, oracle())
    assert m["n_evaluable"] == 5 and m["n_oracle_feasible"] == 3 and m["n_oracle_infeasible"] == 2
    assert m["plan_feasibility_rate"] == pytest.approx(2 / 3)  # i1, i5 valid; i2 not
    assert m["false_rejection_rate"] == 0.0
    assert m["n_itineraries"] == 4
    assert m["hard_violation_rate"] == pytest.approx(2 / 4)  # i2 (arrival), i4 (connector)
    assert m["violations_by_code"] == {"arrival_soc": 1, "connector": 1}


def test_infeasible_handling():
    m = infeasible_handling(ITEMS, RESULTS, oracle())
    assert m["n_oracle_infeasible"] == 2
    assert m["infeasible_handling_accuracy"] == 0.5
    assert m["oracle_reasons"] == {"connector_no_overlap": 2}
    assert m["relaxed_plan_violation_rate"] is None


def test_robust_feasibility():
    m = robust_feasibility(ITEMS, RESULTS, oracle())
    assert m["efficiency_penalty"] == 0.15
    assert m["n_valid_recommended"] == 2
    assert m["robust_plan_rate"] == 0.5  # i1 (25.6 % at +15 %) survives, i5 (15.6 %) does not
    assert m["robust_oracle_feasible_rate"] == 1.0


def test_no_findings_when_planner_and_oracle_agree():
    assert planning_metrics(ITEMS, RESULTS, oracle())["findings"] == []


def test_disagreements_are_reported_as_findings():
    items = [item("f1"), item("f2")]
    results = [
        result("f1", status="infeasible"),
        result("f2", itins=[itinerary(stop("S", depart=90))], verification=VerificationReport(ok=False, violations=[])),
    ]
    kinds = sorted(f["kind"] for f in planning_metrics(items, results, oracle())["findings"])
    assert kinds == ["oracle_feasible_planner_rejected", "verifier_disagreement"]


def test_plan_on_the_front_is_not_a_finding():
    plan = itinerary(stop("S", depart=76))  # exactly the need: equals the oracle's best plan
    assert planning_metrics([item("p")], [result("p", itins=[plan])], oracle())["findings"] == []


def test_plan_better_than_oracle_front_is_a_finding(monkeypatch):
    # If the oracle's front ever misses a valid plan, that is reported (oracle gap), never hidden.
    o = oracle()
    real = o.pareto_plans

    def worse_front(request):
        res = real(request)
        res.plans = [dataclasses.replace(p, total_time_min=p.total_time_min + 60) for p in res.plans]
        return res

    monkeypatch.setattr(o, "pareto_plans", worse_front)
    findings = planning_metrics([item("p")], [result("p", itins=[itinerary(stop("S", depart=76))])], o)["findings"]
    assert [f["kind"] for f in findings] == ["planner_plan_outside_oracle_front"]


def test_unknown_power_sensitivity_excludes_leaf():
    leaf = vehicle(LEAF)
    o = world([station("Q", 60, ports=[port(kw=None)])], vehicles=[vehicle(), leaf])
    items = [item("u1"), item("u2", vehicle_id=LEAF)]
    plan = itinerary(stop("Q", kw=None, depart=76))  # 26 -> 76 % = 25 kWh
    results = [result("u1", itins=[plan]), result("u2", itins=[plan.model_copy(update={"vehicle_id": LEAF})])]
    m = unknown_power_sensitivity(items, results, o)
    assert m["n_items"] == 1 and m["n_leaf_excluded_from_charge_time"] == 1
    assert m["baseline_dc_fast_kw"] == 50.0
    assert m["mean_delta_charge_min_25kw"] == pytest.approx(30.0)  # 60 + 3 vs 30 + 3
    assert m["mean_delta_charge_min_100kw"] == pytest.approx(-11.25)  # capped at 80 kW: 18.75 + 3
    assert m["recommended_on_front_rate_25kw"] == 1.0
    assert m["oracle_fastest_change_rate_100kw"] == 0.0


def test_corridor_buffer_diagnostic():
    o = world([station("FAR", 60, off_km=9.0)])
    m = corridor_buffer_diagnostic([item("b1")], [result("b1", status="infeasible")], o)
    assert m["n_feasibility_changes_without_buffer"] == 1 and m["items_changed_without_buffer"] == ["b1"]


def test_unknown_status_reliance():
    # U: fast but 6 min off-route, status unknown. K: slow, on route. Both are on the front.
    o = world([station("U", 60, off_for_detour(6), status="unknown"), station("K", 60, ports=[port(kw=50)])])
    m = unknown_status_reliance([item("s1")], [result("s1", itins=[itinerary(stop("K", depart=80))])], o)
    assert m["n_oracle_plans"] == 2 and m["oracle_plans_unknown_status"] == 1  # U (fast) and K (slow, on front)
    assert m["items_any_plan_unknown_status_share"] == 1.0
    assert m["items_all_plans_unknown_status_share"] == 0.0
    # Front-based metrics only run where the planner returned a plan.
    none = unknown_status_reliance([item("s1")], [result("s1", status="infeasible")], o)
    assert none["n_oracle_plans"] == 0 and none["items_any_plan_unknown_status_share"] is None
