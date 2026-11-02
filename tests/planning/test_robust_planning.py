"""Tests for the planner's separate +15% energy sensitivity run."""

import pytest


def _planner_inputs(
    pipe_a,
    request_text: str,
    request_id: str,
):
    """Build the real fixture inputs used by both planner entry points."""

    parse_result = pipe_a.parser.parse(request_text, request_id)
    assert not parse_result.needs_input

    request = parse_result.request
    vehicle = pipe_a.store.vehicle(request.vehicle_id)
    route = pipe_a.router.route(request.origin, request.destination)
    candidates = pipe_a.retriever.candidates(route, request)

    for candidate in candidates:
        candidate.detour_min_est = pipe_a.router.detour_min(
            route,
            candidate,
        )

    compatible_candidates, _rejected_candidates = pipe_a.filter.apply(
        candidates,
        request,
        vehicle,
    )

    return request, vehicle, route, compatible_candidates


def _stop_sequence(itinerary) -> tuple[str, ...]:
    """Return a stable station sequence for comparing plan availability."""

    return tuple(stop.station_id for stop in itinerary.stops)


def test_robust_run_increases_leg_energy_by_configured_penalty(pipe_a):
    """The sensitivity run applies exactly the frozen +15% consumption."""

    request, vehicle, route, candidates = _planner_inputs(
        pipe_a,
        "from Phoenix to Flagstaff in my comet with 90%",
        "robust-energy",
    )

    nominal_itineraries = pipe_a.planner.plan(
        request,
        vehicle,
        route,
        candidates,
    )
    robust_itineraries = pipe_a.planner.plan_robust(
        request,
        vehicle,
        route,
        candidates,
    )
    nominal_direct = next(
        itinerary
        for itinerary in nominal_itineraries
        if not itinerary.stops
    )
    robust_direct = next(
        itinerary
        for itinerary in robust_itineraries
        if not itinerary.stops
    )
    configured_multiplier = (
        1.0 + pipe_a.cfg["energy"]["efficiency_penalty_robust"]
    )

    assert robust_direct.legs[0].energy_kwh == pytest.approx(
        nominal_direct.legs[0].energy_kwh * configured_multiplier
    )
    assert robust_direct.arrival_soc_pct < nominal_direct.arrival_soc_pct


def test_robust_run_does_not_change_later_nominal_results(pipe_a):
    """Sensitivity analysis must not mutate planner inputs or normal output."""

    request, vehicle, route, candidates = _planner_inputs(
        pipe_a,
        "from Phoenix to Flagstaff in my spark lr with 65%",
        "robust-isolation",
    )
    nominal_before = pipe_a.planner.plan(
        request,
        vehicle,
        route,
        candidates,
    )

    pipe_a.planner.plan_robust(
        request,
        vehicle,
        route,
        candidates,
    )

    nominal_after = pipe_a.planner.plan(
        request,
        vehicle,
        route,
        candidates,
    )

    assert nominal_after == nominal_before


def test_robust_run_rejects_marginal_plan_but_keeps_safer_option(pipe_a):
    """Higher consumption can require another stop without rejecting the trip."""

    request, vehicle, route, candidates = _planner_inputs(
        pipe_a,
        "from Phoenix to Flagstaff in my leafy with 60%",
        "robust-marginal-plan",
    )
    nominal_sequences = {
        _stop_sequence(itinerary)
        for itinerary in pipe_a.planner.plan(
            request,
            vehicle,
            route,
            candidates,
        )
    }
    robust_sequences = {
        _stop_sequence(itinerary)
        for itinerary in pipe_a.planner.plan_robust(
            request,
            vehicle,
            route,
            candidates,
        )
    }
    marginal_one_stop_plan = ("FIX-ST-002",)
    safer_two_stop_plan = (
        "FIX-ST-002",
        "FIX-ST-005",
    )

    assert marginal_one_stop_plan in nominal_sequences
    assert marginal_one_stop_plan not in robust_sequences
    assert safer_two_stop_plan in nominal_sequences
    assert safer_two_stop_plan in robust_sequences
