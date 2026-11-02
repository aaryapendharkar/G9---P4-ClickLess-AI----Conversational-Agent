"""Tests for comfort-profile top-up behavior at requested meal stops."""

import pytest

from clickless.planning.itinerary_simulation import simulate_itinerary


def _simulate_one_stop(
    pipe_a,
    request_text: str,
    request_id: str,
    profile: str,
    station_id: str,
):
    """Simulate one selected fixture stop with request-specific match scores."""

    parse_result = pipe_a.parser.parse(request_text, request_id)
    assert not parse_result.needs_input

    request = parse_result.request.model_copy(
        update={"tradeoff_profile": profile},
    )
    vehicle = pipe_a.store.vehicle(request.vehicle_id)
    route = pipe_a.router.route(request.origin, request.destination)
    candidates = pipe_a.retriever.candidates(route, request)

    for candidate in candidates:
        for nearby_amenity in candidate.amenities:
            for preference in request.amenities:
                nearby_amenity.match_scores[preference.key] = (
                    pipe_a.matcher.score(
                        preference,
                        nearby_amenity.amenity,
                    )
                )

        candidate.detour_min_est = pipe_a.router.detour_min(
            route,
            candidate,
        )

    compatible_candidates, _rejected_candidates = pipe_a.filter.apply(
        candidates,
        request,
        vehicle,
    )
    selected_candidate = next(
        candidate
        for candidate in compatible_candidates
        if candidate.station.station_id == station_id
    )

    itinerary = simulate_itinerary(
        request,
        vehicle,
        route,
        [selected_candidate],
        pipe_a.cfg["energy"],
        efficiency_penalty=0.0,
    )
    assert itinerary is not None

    return itinerary


def test_comfort_profile_tops_up_to_taper_start_at_requested_food_stop(pipe_a):
    """A relaxed meal stop uses otherwise-idle time for efficient charging."""

    request_text = (
        "from Phoenix to Flagstaff in my spark lr with 65%, "
        "lunch and no rush"
    )
    balanced_itinerary = _simulate_one_stop(
        pipe_a,
        request_text,
        "comfort-balanced-comparison",
        "balanced",
        "FIX-ST-004",
    )
    comfort_itinerary = _simulate_one_stop(
        pipe_a,
        request_text,
        "comfort-top-up",
        "comfort",
        "FIX-ST-004",
    )
    balanced_stop = balanced_itinerary.stops[0]
    comfort_stop = comfort_itinerary.stops[0]
    taper_start_soc_pct = pipe_a.cfg["energy"]["charging_model"][
        "taper_start_soc_pct"
    ]

    assert balanced_stop.depart_soc_pct < taper_start_soc_pct
    assert comfort_stop.depart_soc_pct == pytest.approx(taper_start_soc_pct)
    assert comfort_stop.charge_min_est > balanced_stop.charge_min_est
    assert comfort_itinerary.arrival_soc_pct > balanced_itinerary.arrival_soc_pct


def test_fastest_profile_keeps_just_enough_policy_at_food_stop(pipe_a):
    """Prioritizing speed must not add the comfort-only top-up."""

    request_text = "from Phoenix to Flagstaff in my spark lr with 65%, lunch"
    balanced_itinerary = _simulate_one_stop(
        pipe_a,
        request_text,
        "fastest-balanced-comparison",
        "balanced",
        "FIX-ST-004",
    )
    fastest_itinerary = _simulate_one_stop(
        pipe_a,
        request_text,
        "fastest-just-enough",
        "fastest",
        "FIX-ST-004",
    )

    assert fastest_itinerary.stops[0].depart_soc_pct == pytest.approx(
        balanced_itinerary.stops[0].depart_soc_pct
    )
    assert fastest_itinerary.stops[0].charge_min_est == pytest.approx(
        balanced_itinerary.stops[0].charge_min_est
    )


def test_comfort_profile_does_not_top_up_without_requested_food(pipe_a):
    """Comfort alone must not assume the driver plans to eat at the stop."""

    request_text = "from Phoenix to Flagstaff in my spark lr with 65%, no rush"
    balanced_itinerary = _simulate_one_stop(
        pipe_a,
        request_text,
        "comfort-no-food-balanced",
        "balanced",
        "FIX-ST-004",
    )
    comfort_itinerary = _simulate_one_stop(
        pipe_a,
        request_text,
        "comfort-no-food",
        "comfort",
        "FIX-ST-004",
    )

    assert comfort_itinerary.stops[0].depart_soc_pct == pytest.approx(
        balanced_itinerary.stops[0].depart_soc_pct
    )
    assert comfort_itinerary.stops[0].charge_min_est == pytest.approx(
        balanced_itinerary.stops[0].charge_min_est
    )
