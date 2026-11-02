"""Tests for forward multi-stop search and label-frontier pruning."""

from clickless.planning.label_search import (
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


def test_label_dominates_when_no_metric_is_worse():
    """A uniformly better partial path makes the weaker path unnecessary."""

    better_label = _planning_label((0, 2), 30.0, 70.0, 12.0, 4.0)
    weaker_label = _planning_label((1, 2), 25.0, 75.0, 14.0, 6.0)

    assert label_dominates(better_label, weaker_label)
    assert not label_dominates(weaker_label, better_label)


def test_label_does_not_dominate_when_metrics_trade_off():
    """A faster label must remain if it arrives with less useful charge."""

    faster_lower_charge_label = _planning_label(
        (0, 2),
        20.0,
        65.0,
        10.0,
        4.0,
    )
    slower_higher_charge_label = _planning_label(
        (1, 2),
        30.0,
        70.0,
        10.0,
        4.0,
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

    first_label = _planning_label((0, 2), 25.0, 70.0, 12.0, 4.0)
    second_label = _planning_label((1, 2), 25.0, 70.0, 12.0, 4.0)

    assert not label_dominates(first_label, second_label)
    assert not label_dominates(second_label, first_label)


def test_frontier_discards_dominated_candidate():
    retained_label = _planning_label((0, 2), 30.0, 70.0, 12.0, 4.0)
    dominated_candidate = _planning_label((1, 2), 25.0, 75.0, 14.0, 6.0)

    frontier = retain_nondominated_label(
        [retained_label],
        dominated_candidate,
    )

    assert frontier == [retained_label]


def test_frontier_removes_label_dominated_by_candidate():
    weaker_label = _planning_label((1, 2), 25.0, 75.0, 14.0, 6.0)
    better_candidate = _planning_label((0, 2), 30.0, 70.0, 12.0, 4.0)

    frontier = retain_nondominated_label(
        [weaker_label],
        better_candidate,
    )

    assert frontier == [better_candidate]


def test_frontier_preserves_real_metric_tradeoff():
    faster_lower_charge_label = _planning_label(
        (0, 2),
        20.0,
        65.0,
        10.0,
        4.0,
    )
    slower_higher_charge_label = _planning_label(
        (1, 2),
        30.0,
        70.0,
        10.0,
        4.0,
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
    later_sequence = _planning_label((1, 2), 25.0, 70.0, 12.0, 4.0)
    earlier_sequence = _planning_label((0, 2), 25.0, 70.0, 12.0, 4.0)

    frontier = retain_nondominated_label(
        [later_sequence],
        earlier_sequence,
    )

    assert frontier == [earlier_sequence]


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
