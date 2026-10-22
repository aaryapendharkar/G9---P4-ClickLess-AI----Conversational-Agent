import pytest

from clickless.contracts import ChargerLevel
from clickless.planning.charging_time import estimate_charge_min
from clickless.planning.energy import energy_kwh, leg_feasible, soc_after
from clickless.planning.filters import compatible_ports


def test_energy_model(store):
    v = store.vehicle("fixture-spark-lr-2025")  # 0.19 kWh/km, 74 kWh usable
    assert energy_kwh(100, v) == pytest.approx(19.0)
    assert soc_after(50, 100, v) == pytest.approx(50 - 100 * 19 / 74)
    assert leg_feasible(50, 100, v, min_end_soc_pct=10)
    assert not leg_feasible(20, 100, v, min_end_soc_pct=10)


def test_charge_time_monotonic(store, cfg_a):
    v, m = store.vehicle("fixture-spark-lr-2025"), cfg_a["energy"]["charging_model"]
    t = lambda p, a, b: estimate_charge_min(v, p, ChargerLevel.DC_FAST, a, b, m)  # noqa: E731
    assert t(150, 20, 60) < t(150, 20, 80) < t(150, 20, 95)  # more energy, more time
    assert t(50, 20, 60) > t(150, 20, 60)  # more power, less time
    assert t(350, 20, 60) == t(230, 20, 60)  # capped at the vehicle's max DC power
    assert t(150, 60, 60) == 0.0


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
    r = pipe_a.run(demo_text, "v1")
    it = r.alternatives[0].itinerary
    stop = it.stops[0].model_copy(update={"depart_soc_pct": it.stops[0].arrive_soc_pct})  # skip charging
    bad = it.model_copy(update={"stops": [stop]})
    v = pipe_a.store.vehicle(it.vehicle_id)
    report = pipe_a.verifier.verify(bad, r.request, v)
    assert not report.ok and report.violations[0].code == "arrival_soc"


@pytest.mark.xfail(reason="TODO(Hriday): verifier power + detour checks")
def test_verifier_checks_detour_limit(pipe_a, demo_text):
    r = pipe_a.run(demo_text, "v2")
    req = r.request.model_copy(update={"max_detour_min": 1})
    v = pipe_a.store.vehicle(req.vehicle_id)
    assert not pipe_a.verifier.verify(r.alternatives[0].itinerary, req, v).ok


@pytest.mark.xfail(reason="TODO(Hriday): multi-stop planning")
def test_low_battery_small_car_needs_two_stops(pipe_a):
    r = pipe_a.run("from Phoenix to Flagstaff in my leafy with 60%", "v3")
    assert r.status == "ok" and len(r.alternatives[0].itinerary.stops) >= 2
