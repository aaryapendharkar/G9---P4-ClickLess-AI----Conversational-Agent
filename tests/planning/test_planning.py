import pytest

from clickless.contracts import ChargerLevel
from clickless.planning.charging_time import estimate_charge_min
from clickless.planning.energy import energy_kwh, leg_feasible, soc_after
from clickless.planning.filters import compatible_ports
from clickless.planning.planner import (
    PlanningLabel,
    label_dominates,
    retain_nondominated_label,
)


def _planning_label(
    stop_indices: tuple[int, ...],
    arrival_soc_pct: float,
    drive_min: float,
    charge_min: float,
    detour_min: float,
) -> PlanningLabel:
    """Build a partial label for dominance-focused unit tests."""

    return PlanningLabel(
        stop_indices=stop_indices,
        arrival_soc_pct=arrival_soc_pct,
        total_drive_min=drive_min,
        total_charge_min=charge_min,
        total_detour_min=detour_min,
        satisfied_preference_keys=frozenset({"food"}),
    )


def _generate_unranked_itineraries(pipe_a, request_text: str, request_id: str):
    """Run the planning inputs without ranking away valid alternatives."""

    parse_result = pipe_a.parser.parse(request_text, request_id)
    request = parse_result.request

    vehicle = pipe_a.store.vehicle(request.vehicle_id)
    route = pipe_a.router.route(
        request.origin,
        request.destination,
    )

    candidates = pipe_a.retriever.candidates(route, request)

    for candidate in candidates:
        candidate.detour_min_est = pipe_a.router.detour_min(
            route,
            candidate,
        )

    compatible_candidates, _filter_reasons = pipe_a.filter.apply(
        candidates,
        request,
        vehicle,
    )

    itineraries = pipe_a.planner.plan(
        request,
        vehicle,
        route,
        compatible_candidates,
    )

    return itineraries


def _verify_itinerary(pipe_a, itinerary, request):
    """Supply the verifier with authoritative vehicle and route inputs.

    Individual tests can focus on the field they tamper with instead of
    repeating the setup required by the verifier contract.
    """

    vehicle = pipe_a.store.vehicle(itinerary.vehicle_id)
    route = pipe_a.router.route(
        request.origin,
        request.destination,
    )

    return pipe_a.verifier.verify(
        itinerary,
        request,
        vehicle,
        route,
    )


def test_energy_model(store):
    v = store.vehicle("fixture-spark-lr-2025")  # 0.19 kWh/km, 74 kWh usable
    assert energy_kwh(100, v) == pytest.approx(19.0)
    assert soc_after(50, 100, v) == pytest.approx(50 - 100 * 19 / 74)
    assert leg_feasible(50, 100, v, min_end_soc_pct=10)
    assert not leg_feasible(20, 100, v, min_end_soc_pct=10)


def test_label_dominates_when_no_metric_is_worse():
    """A uniformly better partial path makes the weaker path unnecessary."""

    better_label = _planning_label(
        stop_indices=(0, 2),
        arrival_soc_pct=30.0,
        drive_min=70.0,
        charge_min=12.0,
        detour_min=4.0,
    )
    weaker_label = _planning_label(
        stop_indices=(1, 2),
        arrival_soc_pct=25.0,
        drive_min=75.0,
        charge_min=14.0,
        detour_min=6.0,
    )

    assert label_dominates(
        better_label,
        weaker_label,
    )
    assert not label_dominates(
        weaker_label,
        better_label,
    )


def test_label_does_not_dominate_when_metrics_trade_off():
    """A faster label must remain if it arrives with less useful charge."""

    faster_lower_charge_label = _planning_label(
        stop_indices=(0, 2),
        arrival_soc_pct=20.0,
        drive_min=65.0,
        charge_min=10.0,
        detour_min=4.0,
    )
    slower_higher_charge_label = _planning_label(
        stop_indices=(1, 2),
        arrival_soc_pct=30.0,
        drive_min=70.0,
        charge_min=10.0,
        detour_min=4.0,
    )

    assert not label_dominates(
        faster_lower_charge_label,
        slower_higher_charge_label,
    )
    assert not label_dominates(
        slower_higher_charge_label,
        faster_lower_charge_label,
    )


def test_equivalent_labels_do_not_dominate_each_other():
    """Exact equality is not a strict Pareto improvement."""

    first_label = _planning_label(
        stop_indices=(0, 2),
        arrival_soc_pct=25.0,
        drive_min=70.0,
        charge_min=12.0,
        detour_min=4.0,
    )
    second_label = _planning_label(
        stop_indices=(1, 2),
        arrival_soc_pct=25.0,
        drive_min=70.0,
        charge_min=12.0,
        detour_min=4.0,
    )

    assert not label_dominates(
        first_label,
        second_label,
    )
    assert not label_dominates(
        second_label,
        first_label,
    )


def test_frontier_discards_dominated_candidate():
    """A weaker candidate does not change the retained frontier."""

    retained_label = _planning_label(
        stop_indices=(0, 2),
        arrival_soc_pct=30.0,
        drive_min=70.0,
        charge_min=12.0,
        detour_min=4.0,
    )
    dominated_candidate = _planning_label(
        stop_indices=(1, 2),
        arrival_soc_pct=25.0,
        drive_min=75.0,
        charge_min=14.0,
        detour_min=6.0,
    )

    frontier = retain_nondominated_label(
        [retained_label],
        dominated_candidate,
    )

    assert frontier == [retained_label]


def test_frontier_removes_label_dominated_by_candidate():
    """A uniformly better candidate replaces the weaker retained label."""

    weaker_label = _planning_label(
        stop_indices=(1, 2),
        arrival_soc_pct=25.0,
        drive_min=75.0,
        charge_min=14.0,
        detour_min=6.0,
    )
    better_candidate = _planning_label(
        stop_indices=(0, 2),
        arrival_soc_pct=30.0,
        drive_min=70.0,
        charge_min=12.0,
        detour_min=4.0,
    )

    frontier = retain_nondominated_label(
        [weaker_label],
        better_candidate,
    )

    assert frontier == [better_candidate]


def test_frontier_preserves_real_metric_tradeoff():
    """Neither faster driving nor higher arrival charge is always superior."""

    faster_lower_charge_label = _planning_label(
        stop_indices=(0, 2),
        arrival_soc_pct=20.0,
        drive_min=65.0,
        charge_min=10.0,
        detour_min=4.0,
    )
    slower_higher_charge_label = _planning_label(
        stop_indices=(1, 2),
        arrival_soc_pct=30.0,
        drive_min=70.0,
        charge_min=10.0,
        detour_min=4.0,
    )

    frontier = retain_nondominated_label(
        [faster_lower_charge_label],
        slower_higher_charge_label,
    )

    assert frontier == [
        faster_lower_charge_label,
        slower_higher_charge_label,
    ]


def test_frontier_breaks_equivalent_metrics_by_stop_sequence():
    """Equivalent labels retain the deterministic smaller stop sequence."""

    later_sequence = _planning_label(
        stop_indices=(1, 2),
        arrival_soc_pct=25.0,
        drive_min=70.0,
        charge_min=12.0,
        detour_min=4.0,
    )
    earlier_sequence = _planning_label(
        stop_indices=(0, 2),
        arrival_soc_pct=25.0,
        drive_min=70.0,
        charge_min=12.0,
        detour_min=4.0,
    )

    frontier = retain_nondominated_label(
        [later_sequence],
        earlier_sequence,
    )

    assert frontier == [earlier_sequence]


def test_charge_time_monotonic(store, cfg_a):
    vehicle = store.vehicle("fixture-spark-lr-2025")

    charging_model = cfg_a["energy"]["charging_model"]

    def charge_time(
        power_kw: float,
        start_soc_pct: float,
        target_soc_pct: float,
    ) -> float:
        return estimate_charge_min(
            vehicle,
            power_kw,
            ChargerLevel.DC_FAST,
            start_soc_pct,
            target_soc_pct,
            charging_model,
        )

    assert (
        charge_time(150, 20, 60) < charge_time(150, 20, 80) < charge_time(150, 20, 95)
    )  # more energy, more time
    assert charge_time(50, 20, 60) > charge_time(150, 20, 60)  # more power, less time
    assert charge_time(350, 20, 60) == charge_time(
        230, 20, 60
    )  # capped at the vehicle's max DC power
    assert charge_time(150, 60, 60) == 0.0


def test_filter_reasons(pipe_a, demo_text):
    req = pipe_a.parser.parse(demo_text, "f1").request
    v = pipe_a.store.vehicle(req.vehicle_id)
    route = pipe_a.router.route(req.origin, req.destination)
    cands = pipe_a.retriever.candidates(route, req)
    _, rejected = pipe_a.filter.apply(cands, req, v)
    reasons = {c.station.station_id: why for c, why in rejected}
    assert reasons["FIX-ST-003"] == "connector"  # NACS-only station, CCS car
    assert reasons["FIX-ST-007"].startswith("status")
    assert reasons["FIX-ST-008"].startswith("access")


@pytest.mark.xfail(reason="TODO(Manas, learning swap): documented adapters")
def test_adapter_makes_station_compatible(store):
    comet = store.vehicle("fixture-comet-awd-2026")  # NACS native + CCS1 adapter
    ccs_only = store.station("FIX-ST-001")
    assert compatible_ports(comet, ccs_only)


def test_verifier_catches_tampered_itinerary(pipe_a, demo_text):
    result = pipe_a.run(demo_text, "v1")
    itinerary = result.alternatives[0].itinerary

    tampered_stop = itinerary.stops[0].model_copy(
        update={
            "depart_soc_pct": itinerary.stops[0].arrive_soc_pct,
        }
    )
    tampered_itinerary = itinerary.model_copy(update={"stops": [tampered_stop]})

    report = _verify_itinerary(
        pipe_a,
        tampered_itinerary,
        result.request,
    )

    assert not report.ok
    assert report.violations[0].code == "arrival_soc"


def test_verifier_checks_detour_limit(pipe_a, demo_text):
    result = pipe_a.run(demo_text, "v2")
    request = result.request.model_copy(update={"max_detour_min": 1})
    report = _verify_itinerary(
        pipe_a,
        result.alternatives[0].itinerary,
        request,
    )

    assert not report.ok
    assert any(violation.code == "detour" for violation in report.violations)


def test_low_battery_small_car_has_feasible_two_stop_option(pipe_a):
    itineraries = _generate_unranked_itineraries(
        pipe_a,
        "from Phoenix to Flagstaff in my leafy with 60%",
        "v3",
    )

    two_stop_itineraries = [
        itinerary for itinerary in itineraries if len(itinerary.stops) == 2
    ]

    assert two_stop_itineraries

    for itinerary in two_stop_itineraries:
        stop_positions_km = [stop.along_route_km for stop in itinerary.stops]

        assert stop_positions_km == sorted(stop_positions_km)


def test_planner_search_is_not_capped_at_two_stops(pipe_a):
    """A three-stop fixture path guards against restoring a two-loop cap."""

    itineraries = _generate_unranked_itineraries(
        pipe_a,
        "from Phoenix to Flagstaff in my spark lr with 65%",
        "v-multistop",
    )

    three_stop_itineraries = [
        itinerary for itinerary in itineraries if len(itinerary.stops) == 3
    ]

    assert three_stop_itineraries

    for itinerary in three_stop_itineraries:
        stop_positions_km = [stop.along_route_km for stop in itinerary.stops]

        assert stop_positions_km == sorted(stop_positions_km)


def test_verifier_checks_minimum_power(pipe_a, demo_text):
    result = pipe_a.run(demo_text, "v-power")
    itinerary = result.alternatives[0].itinerary

    request = result.request.model_copy(update={"min_power_kw": 100})
    report = _verify_itinerary(pipe_a, itinerary, request)

    assert not report.ok
    assert any(violation.code == "power" for violation in report.violations)


def test_verifier_rejects_unknown_station(pipe_a, demo_text):
    result = pipe_a.run(demo_text, "v-unknown")
    itinerary = result.alternatives[0].itinerary

    unknown_stop = itinerary.stops[0].model_copy(
        update={"station_id": "DOES-NOT-EXIST"}
    )
    tampered_itinerary = itinerary.model_copy(update={"stops": [unknown_stop]})

    report = _verify_itinerary(
        pipe_a,
        tampered_itinerary,
        result.request,
    )

    assert not report.ok
    assert any(violation.code == "unknown_station" for violation in report.violations)


def test_verifier_rejects_unavailable_station(pipe_a, demo_text):
    result = pipe_a.run(demo_text, "v-status")
    itinerary = result.alternatives[0].itinerary

    unavailable_stop = itinerary.stops[0].model_copy(
        update={
            "station_id": "FIX-ST-007",
            "station_name": "Fixture Munds Park (offline)",
        }
    )
    tampered_itinerary = itinerary.model_copy(update={"stops": [unavailable_stop]})

    report = _verify_itinerary(
        pipe_a,
        tampered_itinerary,
        result.request,
    )

    assert not report.ok
    assert any(violation.code == "status" for violation in report.violations)


def test_verifier_rejects_restricted_station(pipe_a, demo_text):
    result = pipe_a.run(demo_text, "v-access")
    itinerary = result.alternatives[0].itinerary

    restricted_stop = itinerary.stops[0].model_copy(
        update={
            "station_id": "FIX-ST-008",
            "station_name": "Fixture Private Fleet Yard",
        }
    )
    tampered_itinerary = itinerary.model_copy(update={"stops": [restricted_stop]})

    report = _verify_itinerary(
        pipe_a,
        tampered_itinerary,
        result.request,
    )

    assert not report.ok
    assert any(violation.code == "access" for violation in report.violations)


@pytest.mark.parametrize("invalid_departure_soc_pct", [0, 130])
def test_verifier_rejects_invalid_departure_soc(
    pipe_a,
    demo_text,
    invalid_departure_soc_pct,
):
    result = pipe_a.run(demo_text, "v-departure")
    itinerary = result.alternatives[0].itinerary

    impossible_stop = itinerary.stops[0].model_copy(
        update={"depart_soc_pct": invalid_departure_soc_pct}
    )
    tampered_itinerary = itinerary.model_copy(update={"stops": [impossible_stop]})

    report = _verify_itinerary(
        pipe_a,
        tampered_itinerary,
        result.request,
    )

    assert not report.ok
    assert any(violation.code == "departure_soc" for violation in report.violations)


def test_verifier_rejects_broken_leg_continuity(pipe_a, demo_text):
    result = pipe_a.run(demo_text, "v-continuity")
    itinerary = result.alternatives[0].itinerary

    disconnected_outbound_leg = itinerary.legs[1].model_copy(
        update={"start_label": "A different charging station"}
    )
    tampered_itinerary = itinerary.model_copy(
        update={"legs": [itinerary.legs[0], disconnected_outbound_leg]}
    )

    report = _verify_itinerary(
        pipe_a,
        tampered_itinerary,
        result.request,
    )

    assert not report.ok
    assert any(violation.code == "continuity" for violation in report.violations)


def test_verifier_rejects_tampered_leg_distance(pipe_a, demo_text):
    """Energy checks must not trust a shorter distance claimed by the planner."""

    result = pipe_a.run(demo_text, "v-route-distance")
    itinerary = result.alternatives[0].itinerary

    falsified_first_leg = itinerary.legs[0].model_copy(update={"distance_km": 1.0})
    falsified_itinerary = itinerary.model_copy(
        update={
            "legs": [
                falsified_first_leg,
                *itinerary.legs[1:],
            ]
        }
    )

    report = _verify_itinerary(
        pipe_a,
        falsified_itinerary,
        result.request,
    )

    assert not report.ok
    assert any(violation.code == "route_distance" for violation in report.violations)


def test_verifier_rejects_tampered_detour(pipe_a, demo_text):
    """Detour checks must use the route estimate, not planner-provided totals."""

    result = pipe_a.run(demo_text, "v-route-detour")
    itinerary = result.alternatives[0].itinerary

    falsified_first_stop = itinerary.stops[0].model_copy(update={"detour_min": 0.0})
    falsified_itinerary = itinerary.model_copy(
        update={
            "stops": [
                falsified_first_stop,
                *itinerary.stops[1:],
            ],
            "total_detour_min": 0.0,
        }
    )

    report = _verify_itinerary(
        pipe_a,
        falsified_itinerary,
        result.request,
    )

    assert not report.ok
    assert any(violation.code == "detour" for violation in report.violations)
