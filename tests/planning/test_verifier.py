"""Tests for independent complete-itinerary verification."""

import pytest


def _verify_itinerary(pipe_a, itinerary, request):
    """Verify using authoritative vehicle and route inputs."""

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


def test_verifier_catches_tampered_itinerary(pipe_a, demo_text):
    result = pipe_a.run(demo_text, "verifier-arrival")
    itinerary = result.alternatives[0].itinerary
    tampered_stop = itinerary.stops[0].model_copy(
        update={
            "depart_soc_pct": itinerary.stops[0].arrive_soc_pct,
        }
    )
    tampered_itinerary = itinerary.model_copy(
        update={"stops": [tampered_stop]}
    )

    report = _verify_itinerary(
        pipe_a,
        tampered_itinerary,
        result.request,
    )

    assert not report.ok
    assert report.violations[0].code == "arrival_soc"


def test_verifier_checks_detour_limit(pipe_a, demo_text):
    result = pipe_a.run(demo_text, "verifier-detour-limit")
    request = result.request.model_copy(
        update={"max_detour_min": 1}
    )

    report = _verify_itinerary(
        pipe_a,
        result.alternatives[0].itinerary,
        request,
    )

    assert not report.ok
    assert any(violation.code == "detour" for violation in report.violations)


def test_verifier_checks_minimum_power(pipe_a, demo_text):
    result = pipe_a.run(demo_text, "verifier-power")
    itinerary = result.alternatives[0].itinerary
    request = result.request.model_copy(
        update={"min_power_kw": 100}
    )

    report = _verify_itinerary(
        pipe_a,
        itinerary,
        request,
    )

    assert not report.ok
    assert any(violation.code == "power" for violation in report.violations)


def test_verifier_rejects_unknown_station(pipe_a, demo_text):
    result = pipe_a.run(demo_text, "verifier-unknown")
    itinerary = result.alternatives[0].itinerary
    unknown_stop = itinerary.stops[0].model_copy(
        update={"station_id": "DOES-NOT-EXIST"}
    )
    tampered_itinerary = itinerary.model_copy(
        update={"stops": [unknown_stop]}
    )

    report = _verify_itinerary(
        pipe_a,
        tampered_itinerary,
        result.request,
    )

    assert not report.ok
    assert any(
        violation.code == "unknown_station"
        for violation in report.violations
    )


def test_verifier_rejects_unavailable_station(pipe_a, demo_text):
    result = pipe_a.run(demo_text, "verifier-status")
    itinerary = result.alternatives[0].itinerary
    unavailable_stop = itinerary.stops[0].model_copy(
        update={
            "station_id": "FIX-ST-007",
            "station_name": "Fixture Munds Park (offline)",
        }
    )
    tampered_itinerary = itinerary.model_copy(
        update={"stops": [unavailable_stop]}
    )

    report = _verify_itinerary(
        pipe_a,
        tampered_itinerary,
        result.request,
    )

    assert not report.ok
    assert any(violation.code == "status" for violation in report.violations)


def test_verifier_rejects_restricted_station(pipe_a, demo_text):
    result = pipe_a.run(demo_text, "verifier-access")
    itinerary = result.alternatives[0].itinerary
    restricted_stop = itinerary.stops[0].model_copy(
        update={
            "station_id": "FIX-ST-008",
            "station_name": "Fixture Private Fleet Yard",
        }
    )
    tampered_itinerary = itinerary.model_copy(
        update={"stops": [restricted_stop]}
    )

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
    result = pipe_a.run(demo_text, "verifier-departure")
    itinerary = result.alternatives[0].itinerary
    impossible_stop = itinerary.stops[0].model_copy(
        update={"depart_soc_pct": invalid_departure_soc_pct}
    )
    tampered_itinerary = itinerary.model_copy(
        update={"stops": [impossible_stop]}
    )

    report = _verify_itinerary(
        pipe_a,
        tampered_itinerary,
        result.request,
    )

    assert not report.ok
    assert any(
        violation.code == "departure_soc"
        for violation in report.violations
    )


def test_verifier_rejects_broken_leg_continuity(pipe_a, demo_text):
    result = pipe_a.run(demo_text, "verifier-continuity")
    itinerary = result.alternatives[0].itinerary
    disconnected_outbound_leg = itinerary.legs[1].model_copy(
        update={"start_label": "A different charging station"}
    )
    tampered_itinerary = itinerary.model_copy(
        update={
            "legs": [
                itinerary.legs[0],
                disconnected_outbound_leg,
            ]
        }
    )

    report = _verify_itinerary(
        pipe_a,
        tampered_itinerary,
        result.request,
    )

    assert not report.ok
    assert any(
        violation.code == "continuity"
        for violation in report.violations
    )


def test_verifier_rejects_tampered_leg_distance(pipe_a, demo_text):
    """Energy checks must not trust a shorter distance claimed by the planner."""

    result = pipe_a.run(demo_text, "verifier-route-distance")
    itinerary = result.alternatives[0].itinerary
    falsified_first_leg = itinerary.legs[0].model_copy(
        update={"distance_km": 1.0}
    )
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
    assert any(
        violation.code == "route_distance"
        for violation in report.violations
    )


def test_verifier_rejects_tampered_detour(pipe_a, demo_text):
    """Detour checks must use the route estimate, not planner-provided totals."""

    result = pipe_a.run(demo_text, "verifier-route-detour")
    itinerary = result.alternatives[0].itinerary
    falsified_first_stop = itinerary.stops[0].model_copy(
        update={"detour_min": 0.0}
    )
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
