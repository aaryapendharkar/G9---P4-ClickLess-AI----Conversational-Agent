"""Regression tests for the #74 review (Manas): one test per point, named after it.

Hand-built worlds from _oracle_world (vehicle T: 1 % SoC = 2.5 km, detour = 2 + 3.9 min per
off-route km). Every oracle query also runs through TwinOracle, so each case additionally checks
that the indexed path and the exhaustive reference still agree.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _oracle_world import (
    ENERGY,
    KM_PER_DEG,
    PROV,
    TwinOracle,
    itinerary,
    off_for_detour,
    port,
    req,
    result,
    station,
    stop,
    vehicle,
    world,
)

from clickless.contracts import LatLon, Relaxation, Route, VerificationReport
from clickless.data.loaders import DataStore
from evaluation.harness import _safe
from evaluation.metrics.planning import (
    corridor_buffer_diagnostic,
    feasibility_and_violations,
    infeasible_handling,
    planning_metrics,
    relaxed_request,
    request_from_item,
    unknown_power_sensitivity,
    unknown_status_reliance,
)
from evaluation.oracle.label_setting import FeasibilityResult, LabelSettingOracle, OracleConfig, OracleUnavailable
from evaluation.schema import BenchmarkItem


def item(item_id, **gold):
    g = {"origin": "a", "destination": "b", "vehicle_id": "t", "start_soc_pct": 50, **gold}
    return BenchmarkItem(
        id=item_id, author="ian", category="rural", route_type="rural", text=item_id, gold_request=g,
        clarification_required=False,
    )


def with_route_source(source, stations=()) -> TwinOracle:
    """world() with a custom route source (the default always returns the 200 km test route)."""
    store = DataStore(list(stations), [vehicle()], [], {"a": (0.0, 0.0), "b": (0.0, 200 / KM_PER_DEG)}, Path("."))

    def make(method):
        return LabelSettingOracle(store, OracleConfig(energy=ENERGY, method=method), route_source=source)

    return TwinOracle(make("indexed"), make("exhaustive"))


def raiser(exc):
    def source(_o, _d):
        raise exc

    return source


# --------------------------------------------------------------------------- 1. errors that abort the run


class OSRMNoRouteError(RuntimeError):  # same names as the router's, without importing planner code
    pass


class OSRMTemporaryError(RuntimeError):
    pass


@pytest.mark.parametrize(
    "exc,reason",
    [
        (OSRMNoRouteError("no route"), "no_route"),
        (LookupError("route not cached"), "no_route"),
        (OSRMTemporaryError("timeout"), "route_error"),
        (ValueError("place has no coordinates"), "route_error"),
    ],
)
def test_p1_route_source_errors_make_the_item_not_evaluable(exc, reason):
    fr = with_route_source(raiser(exc)).is_feasible(req())
    assert (fr.feasible, fr.evaluable, fr.reason) == (False, False, reason)
    assert type(exc).__name__ in fr.detail


def test_p1_route_error_is_counted_not_fatal():
    o = with_route_source(raiser(OSRMTemporaryError("timeout")))
    m = planning_metrics([item("e1")], [result("e1")], o)
    assert m["feasibility"]["skipped_by_reason"] == {"route_error": 1}
    assert [f["kind"] for f in m["findings"]] == ["oracle_nothing_evaluable"]


def test_p1_safe_isolates_any_metric_error():
    def broken(*_):
        raise TypeError("'<' not supported between instances of 'NoneType' and 'str'")

    assert _safe(broken) == {"error": "TypeError: '<' not supported between instances of 'NoneType' and 'str'"}


# --------------------------------------------------------------------------- 2. edge inputs


def _route(points, km=200.0, duration=120.0) -> Route:
    geometry = [LatLon(lat=0.0, lon=lon / KM_PER_DEG) for lon in points]
    return Route(
        route_id="edge", origin=geometry[0], destination=geometry[-1], geometry=geometry, distance_km=km,
        duration_min=duration, provenance=PROV,
    )


@pytest.mark.parametrize(
    "route",
    [_route([0.0], km=5.0), _route([0.0, 0.0], km=0.0), _route([0.0, 0.0], km=3.0)],
    ids=["one-point geometry", "same place (0 km)", "zero-length geometry"],
)
def test_p2_degenerate_route_is_not_evaluable(route):
    o = with_route_source(lambda _o, _d: route, [station("S", 60)])
    fr = o.is_feasible(req())
    assert (fr.evaluable, fr.reason) == (False, "degenerate_route")
    assert o.pareto_plans(req()).plans == []
    with pytest.raises(OracleUnavailable, match="degenerate_route"):
        o.check_itinerary(req(), itinerary())


# --------------------------------------------------------------------------- 3. power of the port used


def test_p3_min_power_is_checked_on_the_port_the_stop_uses():
    # Manas's case: CCS1 50 kW + 150 kW, min_power_kw=100, the plan uses the 50 kW port.
    o = world([station("M", 60, ports=[port(kw=50), port(kw=150)])])
    on_50 = o.check_itinerary(req(min_power_kw=100), itinerary(stop("M", kw=50, depart=90)))
    assert not on_50.ok and [v.code for v in on_50.violations] == ["power"]
    assert "50" in on_50.violations[0].detail
    on_150 = o.check_itinerary(req(min_power_kw=100), itinerary(stop("M", kw=150, depart=90)))
    assert on_150.ok


# --------------------------------------------------------------------------- 4. verifier vs the parsed request


def test_p4_verifier_compared_on_parsed_request_and_parse_difference_reported():
    o = world([station("D6", 60, off_for_detour(6), ports=[port(kw=50)])])
    gold = item("v1", max_detour_min=5)
    parsed = request_from_item(gold).model_copy(update={"max_detour_min": None})  # the parser dropped "max 5 min"
    res = result("v1", itins=[itinerary(stop("D6", detour=6.0, depart=90))], verification=VerificationReport(ok=True))
    res = res.model_copy(update={"request": parsed})
    kinds = {f["kind"]: f["detail"] for f in planning_metrics([gold], [res], o)["findings"]}
    # The verifier saw no detour limit and said ok; so does the oracle on the same request.
    assert "verifier_disagreement" not in kinds
    assert kinds["parse_changed_constraints"] == "max_detour_min: gold 5.0, parsed None"
    # Against the gold request the plan still counts as a violation (parser errors count against the system).
    assert feasibility_and_violations([gold], [res], o)["violations_by_code"] == {"detour": 1}


def test_p4_real_verifier_disagreement_is_still_reported():
    o = world([station("S", 60, ports=[port(kw=50)])])
    gold = item("v2")
    bad = result("v2", itins=[itinerary(stop("S", depart=90))], verification=VerificationReport(ok=False))
    bad = bad.model_copy(update={"request": request_from_item(gold)})
    findings = planning_metrics([gold], [bad], o)["findings"]
    assert [f["kind"] for f in findings] == ["verifier_disagreement"]
    assert "parsed request" in findings[0]["detail"]


# --------------------------------------------------------------------------- 5. tolerance gap


def test_p5_plan_valid_only_within_tolerance_is_not_called_outside_the_front():
    o = world([station("S", 60, ports=[port(kw=50)])])
    plan = itinerary(stop("S", depart=75.8))  # arrives with 19.8 % against the 20 % default floor
    report = o.check_itinerary(req(), plan)
    assert report.ok and not report.strict_ok and report.strict_violations == ["arrival_soc"]
    kinds = [f["kind"] for f in planning_metrics([item("t1")], [result("t1", itins=[plan])], o)["findings"]]
    assert kinds == ["plan_passes_only_within_tolerance"]  # not planner_plan_outside_oracle_front


def test_p5_within_tolerance_on_infeasible_request_is_not_passes_check():
    # 140 km needs 56 %; start 75 % and no station: arrives with 19 % against a 19.4 % floor, so the
    # oracle says infeasible, while the check (0.5 % SoC tolerance) passes the direct plan.
    o = world([], route_km=140.0)
    gold = item("t2", start_soc_pct=75, arrival_soc_pct_min=19.4)
    kinds = [f["kind"] for f in planning_metrics([gold], [result("t2", itins=[itinerary()])], o)["findings"]]
    assert "oracle_infeasible_plan_passes_check" not in kinds
    assert kinds == ["plan_passes_only_within_tolerance"]


# --------------------------------------------------------------------------- 6. no-charge trip with a disjoint override


def test_p6_direct_drive_is_feasible_even_with_a_disjoint_override():
    # Manas's case: 95 % on a 100 km trip (needs 40 %), connectors_override=['CHADEMO'].
    o = world([station("X", 50, ports=[port("CHADEMO")])], route_km=100.0)
    request = req(start=95, route_km=100.0, connectors_override=["CHADEMO"])
    fr = o.is_feasible(request)
    assert fr.feasible and fr.witness == []
    assert [p.station_ids for p in o.pareto_plans(request).plans] == [()]
    assert o.check_itinerary(request, itinerary()).ok
    # A trip that needs a charge is still connector_no_overlap.
    needs_charge = req(start=30, route_km=100.0, connectors_override=["CHADEMO"])
    assert o.is_feasible(needs_charge).reason == "connector_no_overlap"


# --------------------------------------------------------------------------- 7. truthiness


def test_p7_feasibility_result_truthiness():
    assert not FeasibilityResult(False, "energy_gap")
    assert FeasibilityResult(True)
    o = world([station("S", 60, ports=[port(kw=50)])])
    assert o.is_feasible(req()) and not o.is_feasible(req(start=5))


# --------------------------------------------------------------------------- 8. ties prefer known status


def test_p8_exact_tie_prefers_known_status_over_station_id_order():
    # Same place, same port: "A-UNK" sorts first but its status is UNKNOWN.
    o = world([station("A-UNK", 60, ports=[port(kw=50)], status="unknown"), station("B-OK", 60, ports=[port(kw=50)])])
    assert [p.station_ids for p in o.pareto_plans(req()).plans] == [("B-OK",)]
    m = unknown_status_reliance([item("u")], [result("u", itins=[itinerary(stop("B-OK", depart=80))])], o)
    assert m["oracle_plans_unknown_status"] == 0 and m["items_any_plan_unknown_status_share"] == 0.0


def test_p8_unknown_station_still_wins_when_it_is_strictly_better():
    o = world([station("A-UNK", 60, ports=[port(kw=150)], status="unknown"), station("B-OK", 60, ports=[port(kw=50)])])
    assert [p.station_ids for p in o.pareto_plans(req()).plans] == [("A-UNK",)]


# --------------------------------------------------------------------------- 9. skipped items are visible


def test_p9_skipped_items_are_listed_with_reasons():
    o = world([station("S", 60, ports=[port(kw=50)])])
    incomplete = BenchmarkItem(
        id="c1", author="ian", category="incomplete", route_type="rural", text="c1", gold_request={"origin": "a"},
        clarification_required=True,
    )
    m = feasibility_and_violations([item("k1"), incomplete, item("k3")], [result("k1"), result("c1")], o)
    assert m["n_items"] == 3 and m["n_evaluable"] == 1 and m["n_skipped"] == 2
    assert m["skipped_by_reason"] == {"clarification_required": 1, "no_result": 1}
    assert sorted(s["item_id"] for s in m["items_skipped"]) == ["c1", "k3"]


def test_p9_empty_routes_is_not_a_clean_run():
    o = with_route_source(lambda _o, _d: None, [station("S", 60)])  # cache mode with an empty routes/
    m = planning_metrics([item("r1"), item("r2")], [result("r1"), result("r2")], o)
    assert m["feasibility"]["n_evaluable"] == 0 and m["feasibility"]["skipped_by_reason"] == {"no_route": 2}
    assert m["findings"] == [
        {"item_id": None, "kind": "oracle_nothing_evaluable", "detail": "0 of 2 items evaluable: {'no_route': 2}"}
    ]


# --------------------------------------------------------------------------- 10. relaxed plans vs the relaxed request


def _relaxed_result(item_id, changes, plan):
    alts = result(item_id, itins=[plan]).alternatives
    return result(item_id, status="infeasible").model_copy(
        update={"relaxation": Relaxation(changes=changes, penalty=3, alternatives=alts)}
    )


D25 = station("D25", 60, off_for_detour(25), ports=[port(kw=50)])  # a 25-minute detour


@pytest.mark.parametrize(
    "changes,rate,unparseable",
    [
        (["max detour 10 -> 15 min"], 1.0, 0),  # relaxed to 15, plan detours 25: violation
        (["max detour 10 -> 30 min"], 0.0, 0),  # relaxed enough: fine
        (["widen the detour a bit"], 1.0, 1),  # cannot be read: never a pass
    ],
)
def test_p10_relaxed_plan_checked_against_relaxed_request(changes, rate, unparseable):
    o = world([D25])
    gold = item("x1", max_detour_min=10)
    assert not o.is_feasible(request_from_item(gold)).feasible
    res = _relaxed_result("x1", changes, itinerary(stop("D25", detour=25.0, depart=90)))
    m = infeasible_handling([gold], [res], o)
    assert m["n_relaxed_itineraries"] == 1 and m["relaxed_plan_violation_rate"] == rate
    assert m["n_relaxation_unparseable"] == unparseable
    kinds = [f["kind"] for f in planning_metrics([gold], [res], o)["findings"]]
    assert ("relaxation_unparseable" in kinds) == bool(unparseable)


def test_p10_relaxed_request_parses_every_change_form():
    base = request_from_item(item("p", max_detour_min=10, arrival_soc_pct_min=25,
                                  amenities=[{"category": "food"}, {"category": "coffee"}]))
    changes = ["drop amenity: coffee", "max detour 10 -> 15 min", "arrival charge 25% -> 20%"]
    relaxed, err = relaxed_request(base, changes)
    assert err is None
    assert (relaxed.max_detour_min, relaxed.arrival_soc_pct_min) == (15.0, 20.0)
    assert [a.key for a in relaxed.amenities] == ["food"]
    missing = "change 'drop amenity: lodging': the request has no amenity 'lodging'"
    assert relaxed_request(base, ["drop amenity: lodging"])[1] == missing


# --------------------------------------------------------------------------- 11. reported baseline


def test_p11_baseline_reports_the_override_actually_used():
    o = world([station("Q", 60, ports=[port(kw=None)])], unknown_power_kw={"dc_fast": 40.0})
    m = unknown_power_sensitivity([item("b")], [result("b", itins=[itinerary(stop("Q", kw=None, depart=76))])], o)
    assert m["baseline_dc_fast_kw"] == 40.0


# --------------------------------------------------------------------------- 12. bounded diagnostic


def test_p12_diagnostic_rechecks_only_infeasible_items_with_a_finite_buffer():
    o = world([station("FAR", 60, off_km=9.0), station("NEAR", 60, ports=[port(kw=50)])])
    far_only = world([station("FAR", 60, off_km=9.0)])
    m = corridor_buffer_diagnostic([item("f")], [result("f")], o)
    assert m["n_rechecked"] == 0  # feasible: more stations cannot make it infeasible
    m = corridor_buffer_diagnostic([item("g")], [result("g", status="infeasible")], far_only)
    assert m["n_rechecked"] == 1 and m["items_feasible_with_wide_buffer"] == ["g"]
    assert m["diagnostic_buffer_km"] == 50.0  # configs/evaluation.yaml oracle.diagnostic_buffer_km


def test_p12_diagnostic_buffer_comes_from_evaluation_config():
    from clickless.config import load_config

    cfg = load_config("A")
    store = DataStore([], [vehicle()], [], {}, Path("."))
    o = LabelSettingOracle.from_config(cfg, store=store, route_source=lambda *_: None)
    assert o.cfg.diagnostic_buffer_km == float(cfg["evaluation"]["oracle"]["diagnostic_buffer_km"])
