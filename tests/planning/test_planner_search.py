"""Integration tests for forward multi-stop planner search."""


def _generate_unranked_itineraries(
    pipe_a,
    request_text: str,
    request_id: str,
):
    """Run planning inputs without ranking away valid alternatives."""

    parse_result = pipe_a.parser.parse(request_text, request_id)
    request = parse_result.request
    vehicle = pipe_a.store.vehicle(request.vehicle_id)
    route = pipe_a.router.route(request.origin, request.destination)
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

    return pipe_a.planner.plan(
        request,
        vehicle,
        route,
        compatible_candidates,
    )


def test_low_battery_small_car_has_feasible_two_stop_option(pipe_a):
    itineraries = _generate_unranked_itineraries(
        pipe_a,
        "from Phoenix to Flagstaff in my leafy with 60%",
        "two-stop-search",
    )
    two_stop_itineraries = [
        itinerary
        for itinerary in itineraries
        if len(itinerary.stops) == 2
    ]

    assert two_stop_itineraries

    for itinerary in two_stop_itineraries:
        stop_positions_km = [
            stop.along_route_km
            for stop in itinerary.stops
        ]
        assert stop_positions_km == sorted(stop_positions_km)


def test_planner_search_is_not_capped_at_two_stops(pipe_a):
    """A three-stop fixture path guards against restoring a two-loop cap."""

    itineraries = _generate_unranked_itineraries(
        pipe_a,
        "from Phoenix to Flagstaff in my spark lr with 65%",
        "multi-stop-search",
    )
    three_stop_itineraries = [
        itinerary
        for itinerary in itineraries
        if len(itinerary.stops) == 3
    ]

    assert three_stop_itineraries

    for itinerary in three_stop_itineraries:
        stop_positions_km = [
            stop.along_route_km
            for stop in itinerary.stops
        ]
        assert stop_positions_km == sorted(stop_positions_km)
