"""Integration tests for the pipeline's reusable request-solving path.

These tests call the helper below ``Pipeline.run`` but still use the real
fixture retriever, filter, planner, verifier, ranker, and trade-off selector.
That makes them a focused guard for the solver that relaxation will reuse.
"""

from clickless.contracts import PlanResult


def _build_solver_inputs(pipe_a, request_text: str, request_id: str):
    """Parse a complete request and load its vehicle and base route."""

    parse_result = pipe_a.parser.parse(request_text, request_id)
    assert not parse_result.needs_input

    request = parse_result.request
    vehicle = pipe_a.store.vehicle(request.vehicle_id)
    route = pipe_a.router.route(request.origin, request.destination)

    return request, vehicle, route


def test_solver_returns_ranked_alternatives_and_rejection_notes(pipe_a, demo_text):
    """A feasible request crosses every stage of the extracted solve path."""

    request, vehicle, route = _build_solver_inputs(
        pipe_a,
        demo_text,
        "solver-feasible",
    )
    completed_phases: list[str] = []

    alternatives, rejection_notes = pipe_a._solve_request(
        request,
        vehicle,
        route,
        completed_phases.append,
    )

    assert alternatives
    assert alternatives[0].label == "Recommended"
    assert alternatives[0].itinerary.request_id == request.request_id
    assert completed_phases == ["retrieve", "plan+verify", "rank"]
    assert "skipped FIX-ST-008: access:restricted" in rejection_notes


def test_solver_returns_no_alternatives_when_power_requirement_is_impossible(
    pipe_a,
    demo_text,
):
    """An infeasible request stops before ranking and explains rejections."""

    request, vehicle, route = _build_solver_inputs(
        pipe_a,
        demo_text,
        "solver-infeasible",
    )
    impossible_request = request.model_copy(
        update={"min_power_kw": 1_000.0},
    )
    completed_phases: list[str] = []

    alternatives, rejection_notes = pipe_a._solve_request(
        impossible_request,
        vehicle,
        route,
        completed_phases.append,
    )

    assert alternatives == []
    assert completed_phases == ["retrieve", "plan+verify"]
    assert any(note.endswith(": power") for note in rejection_notes)


def test_pipeline_offers_cheapest_feasible_detour_relaxation(pipe_a, demo_text):
    """The pipeline suggests, but does not silently apply, a relaxed request."""

    request, vehicle, route = _build_solver_inputs(
        pipe_a,
        demo_text,
        "pipeline-relaxation",
    )
    strict_request = request.model_copy(
        update={"max_detour_min": 0.0},
    )
    result = PlanResult(
        request_id=strict_request.request_id,
        config=pipe_a.name,
        status="ok",
        request=strict_request,
    )

    result = pipe_a.plan_request(
        strict_request,
        result,
    )

    # The original request remains infeasible. Relaxed alternatives are kept
    # inside the proposal until the driver explicitly accepts the change.
    assert result.status == "infeasible"
    assert result.alternatives == []
    assert result.relaxation is not None
    assert result.relaxation.changes == ["max detour 0 -> 5 min"]
    assert result.relaxation.penalty == 3.0
    assert result.relaxation.alternatives
    assert "If you allow: max detour 0 -> 5 min" in result.explanation

    relaxed_request = strict_request.model_copy(
        update={"max_detour_min": 5.0},
    )

    for alternative in result.relaxation.alternatives:
        verification = pipe_a.verifier.verify(
            alternative.itinerary,
            relaxed_request,
            vehicle,
            route,
        )
        assert verification.ok


def test_pipeline_does_not_relax_impossible_power_requirement(pipe_a, demo_text):
    """Relaxation must not weaken a hard charging-power requirement."""

    request, _vehicle, _route = _build_solver_inputs(
        pipe_a,
        demo_text,
        "pipeline-hard-constraint",
    )
    impossible_request = request.model_copy(
        update={"min_power_kw": 1_000.0},
    )
    result = PlanResult(
        request_id=impossible_request.request_id,
        config=pipe_a.name,
        status="ok",
        request=impossible_request,
    )

    result = pipe_a.plan_request(
        impossible_request,
        result,
    )

    assert result.status == "infeasible"
    assert result.alternatives == []
    assert result.relaxation is None
    assert result.request.min_power_kw == 1_000.0
