import pytest

from clickless.contracts import ChargerLevel
from clickless.planning.charging_time import estimate_charge_min
from clickless.planning.energy import energy_kwh, leg_feasible, soc_after
from clickless.planning.filters import compatible_ports


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


def test_energy_model(store):
    v = store.vehicle("fixture-spark-lr-2025")  # 0.19 kWh/km, 74 kWh usable
    assert energy_kwh(100, v) == pytest.approx(19.0)
    assert soc_after(50, 100, v) == pytest.approx(50 - 100 * 19 / 74)
    assert leg_feasible(50, 100, v, min_end_soc_pct=10)
    assert not leg_feasible(20, 100, v, min_end_soc_pct=10)


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

    vehicle = pipe_a.store.vehicle(itinerary.vehicle_id)
    report = pipe_a.verifier.verify(
        tampered_itinerary,
        result.request,
        vehicle,
    )

    assert not report.ok
    assert report.violations[0].code == "arrival_soc"


def test_verifier_checks_detour_limit(pipe_a, demo_text):
    result = pipe_a.run(demo_text, "v2")
    request = result.request.model_copy(update={"max_detour_min": 1})
    vehicle = pipe_a.store.vehicle(request.vehicle_id)
    report = pipe_a.verifier.verify(
        result.alternatives[0].itinerary,
        request,
        vehicle,
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


def test_verifier_checks_minimum_power(pipe_a, demo_text):
    result = pipe_a.run(demo_text, "v-power")
    itinerary = result.alternatives[0].itinerary

    request = result.request.model_copy(update={"min_power_kw": 100})
    vehicle = pipe_a.store.vehicle(request.vehicle_id)

    report = pipe_a.verifier.verify(itinerary, request, vehicle)

    assert not report.ok
    assert any(violation.code == "power" for violation in report.violations)


def test_verifier_rejects_unknown_station(pipe_a, demo_text):
    result = pipe_a.run(demo_text, "v-unknown")
    itinerary = result.alternatives[0].itinerary

    unknown_stop = itinerary.stops[0].model_copy(
        update={"station_id": "DOES-NOT-EXIST"}
    )
    tampered_itinerary = itinerary.model_copy(update={"stops": [unknown_stop]})

    vehicle = pipe_a.store.vehicle(itinerary.vehicle_id)
    report = pipe_a.verifier.verify(
        tampered_itinerary,
        result.request,
        vehicle,
    )

    assert not report.ok
    assert any(violation.code == "unknown_station" for violation in report.violations)
