"""Planning and independent-verification regressions for issue #136.

An explicitly important wheelchair-accessible restroom is mandatory. Softer
wording remains a ranked preference, and an unavailable hard requirement is
only proposed as an approval-gated relaxation.
"""

from clickless.contracts import PlanResult


def _with_important_accessible_restroom(request):
    """Promote only the requested accessible restroom to a hard requirement."""

    promoted_amenities = [
        (
            preference.model_copy(update={"priority": "important"})
            if preference.key == "restroom_wheelchair"
            else preference
        )
        for preference in request.amenities
    ]
    return request.model_copy(update={"amenities": promoted_amenities}, deep=True)


def _satisfies_required_accessibility(itinerary) -> bool:
    """Whether any stop reports a positive accessible-restroom match."""

    return any(
        stop.satisfied_preferences.get("restroom_wheelchair", 0.0) > 0
        for stop in itinerary.stops
    )


def test_planner_returns_only_accessibility_compliant_alternatives(pipe_a, demo_text):
    """A hard accessibility need applies to every displayed alternative."""

    baseline_result = pipe_a.run(
        demo_text, request_id="required-accessibility-baseline"
    )
    request = _with_important_accessible_restroom(baseline_result.request)
    result = pipe_a.plan_request(
        request,
        PlanResult(
            request_id=request.request_id,
            config="A",
            status="ok",
            request=request,
        ),
    )

    assert result.status == "ok"
    assert result.alternatives
    assert all(
        _satisfies_required_accessibility(alternative.itinerary)
        for alternative in result.alternatives
    )


def test_nice_to_have_accessibility_remains_a_soft_preference(pipe_a, demo_text):
    """Soft accessibility wording may still produce a faster alternative without it."""

    result = pipe_a.run(demo_text, request_id="soft-accessibility-preference")

    assert result.status == "ok"
    assert any(
        not _satisfies_required_accessibility(alternative.itinerary)
        for alternative in result.alternatives
    )


def test_verifier_rejects_a_stop_without_required_accessibility(pipe_a, demo_text):
    """The verifier must catch a planner itinerary that omits the hard need."""

    baseline_result = pipe_a.run(
        demo_text, request_id="required-accessibility-verifier"
    )
    request = _with_important_accessible_restroom(baseline_result.request)
    itinerary_without_accessible_restroom = next(
        alternative.itinerary
        for alternative in baseline_result.alternatives
        if not _satisfies_required_accessibility(alternative.itinerary)
    )
    vehicle = pipe_a.store.vehicle(itinerary_without_accessible_restroom.vehicle_id)
    route = pipe_a.router.route(request.origin, request.destination)

    report = pipe_a.verifier.verify(
        itinerary_without_accessible_restroom,
        request,
        vehicle,
        route,
    )

    assert not report.ok
    assert any(
        violation.code == "access"
        and "wheelchair-accessible restroom" in violation.detail
        for violation in report.violations
    )


def test_pipeline_gives_verifier_the_configured_amenity_radius(pipe_a):
    """Retrieval and independent verification must share the nearby radius."""

    assert (
        pipe_a.verifier.amenity_radius_m == pipe_a.cfg["retrieval"]["amenity_radius_m"]
    )


def test_missing_required_accessibility_is_only_offered_as_a_relaxation(
    pipe_a,
    demo_text,
    monkeypatch,
):
    """An unavailable hard need is proposed for approval, never silently removed."""

    baseline_result = pipe_a.run(
        demo_text,
        request_id="required-accessibility-relaxation",
    )
    request = _with_important_accessible_restroom(baseline_result.request)

    # Build a deterministic world where no restroom has verified wheelchair
    # access. Replacing the list also makes the retriever rebuild its amenity
    # index instead of retaining links from the original fixture world.
    amenities_without_accessible_restrooms = [
        (
            amenity.model_copy(update={"wheelchair": "unknown"})
            if amenity.category.value == "restroom"
            else amenity
        )
        for amenity in pipe_a.store.amenities
    ]
    monkeypatch.setattr(
        pipe_a.store,
        "amenities",
        amenities_without_accessible_restrooms,
    )

    result = pipe_a.plan_request(
        request,
        PlanResult(
            request_id=request.request_id,
            config="A",
            status="ok",
            request=request,
        ),
    )

    assert result.status == "infeasible"
    assert result.relaxation is not None
    assert any(
        change == "drop amenity: restroom_wheelchair"
        for change in result.relaxation.changes
    )
    assert "If you allow:" in result.explanation

    # Planning a relaxed alternative must not mutate the original request.
    required_restroom = next(
        preference
        for preference in request.amenities
        if preference.key == "restroom_wheelchair"
    )
    assert required_restroom.priority == "important"
