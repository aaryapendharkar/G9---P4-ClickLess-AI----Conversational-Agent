import pytest

from clickless.contracts import Itinerary, PlanCost, TripRequest
from clickless.ranking.cost import plan_cost, resolve_profile
from clickless.ranking.tradeoffs import TradeoffSelector, dominates


def _test_itinerary(
    itinerary_id: str,
    total_time_min: float,
    detour_min: float,
    charge_min: float,
) -> Itinerary:
    """Build a minimal itinerary for selector-focused unit tests."""

    return Itinerary(
        itinerary_id=itinerary_id,
        request_id="tradeoff-unit-test",
        vehicle_id="test-vehicle",
        stops=[],
        legs=[],
        total_drive_min=total_time_min - charge_min,
        total_charge_min=charge_min,
        total_detour_min=detour_min,
        arrival_soc_pct=20.0,
    )


def _test_cost(
    total_min: float,
    amenity_satisfaction: float = 1.0,
) -> PlanCost:
    """Build a minimal cost without invoking the full planning pipeline."""

    return PlanCost(
        profile="balanced",
        drive_min=total_min,
        charge_min=0.0,
        detour_penalty_min=0.0,
        stop_overhead_min=0.0,
        amenity_credit_min=0.0,
        evidence_credit_min=0.0,
        total_min=total_min,
        amenity_satisfaction=amenity_satisfaction,
    )


def _test_request() -> TripRequest:
    """Return a request with no amenities for selector-only tests."""

    return TripRequest(
        request_id="tradeoff-unit-test",
        raw_text="selector unit test",
    )


def _plans(pipe_a, text, rid="r1"):
    req = pipe_a.parser.parse(text, rid).request
    v = pipe_a.store.vehicle(req.vehicle_id)
    route = pipe_a.router.route(req.origin, req.destination)
    cands = pipe_a.retriever.candidates(route, req)
    for c in cands:
        c.detour_min_est = pipe_a.router.detour_min(route, c)
        for na in c.amenities:
            for p in req.amenities:
                na.match_scores[p.key] = pipe_a.matcher.score(p, na.amenity)
    kept, _ = pipe_a.filter.apply(cands, req, v)
    return req, pipe_a.planner.plan(req, v, route, kept)


def test_cost_terms_add_up(pipe_a, demo_text, cfg_a):
    req, plans = _plans(pipe_a, demo_text)
    c = plan_cost(plans[0], req, cfg_a["tradeoffs"])
    penalties = c.drive_min + c.charge_min + c.detour_penalty_min + c.stop_overhead_min
    expected = penalties - c.amenity_credit_min - c.evidence_credit_min
    assert c.total_min == pytest.approx(expected)


def test_amenity_credit_only_for_requested_and_accessible(
    pipe_a,
    demo_text,
    cfg_a,
):
    request, itineraries = _plans(pipe_a, demo_text)

    single_stop_itineraries_by_station = {
        itinerary.stops[0].station_id: itinerary
        for itinerary in itineraries
        if len(itinerary.stops) == 1
    }

    camp_verde_cost = plan_cost(
        single_stop_itineraries_by_station["FIX-ST-004"],
        request,
        cfg_a["tradeoffs"],
    )

    anthem_cost = plan_cost(
        single_stop_itineraries_by_station["FIX-ST-001"],
        request,
        cfg_a["tradeoffs"],
    )

    assert camp_verde_cost.amenity_credit_min == 15 + 20
    assert anthem_cost.amenity_credit_min == 0

    black_canyon_cost = plan_cost(
        single_stop_itineraries_by_station["FIX-ST-002"],
        request,
        cfg_a["tradeoffs"],
    )

    assert black_canyon_cost.amenity_credit_min == 15


def test_weight_override(cfg_a, pipe_a, demo_text):
    req, _ = _plans(pipe_a, demo_text)
    req = req.model_copy(update={"weight_overrides": {"amenity_value_min.food": 40}})
    assert resolve_profile(cfg_a["tradeoffs"], req)["amenity_value_min"]["food"] == 40
    with pytest.raises(KeyError):
        resolve_profile(
            cfg_a["tradeoffs"], req.model_copy(update={"weight_overrides": {"typo": 1}})
        )


def test_profiles_change_the_recommendation(pipe_a, cfg_a):
    # Lunch only: Anthem is ~10 min faster (150 kW) but has no food; Camp Verde has food (50 kW).
    req, plans = _plans(pipe_a, "from Phoenix to Flagstaff in my spark lr, 65%, lunch")
    best = {}
    for prof in ("fastest", "comfort"):
        r = req.model_copy(update={"tradeoff_profile": prof})
        best[prof] = min(
            plans, key=lambda p: plan_cost(p, r, cfg_a["tradeoffs"]).total_min
        ).itinerary_id
    assert best["fastest"].endswith("FIX-ST-001") and best["comfort"].endswith(
        "FIX-ST-004"
    )


def test_dominates():
    axes = {"t": "min", "s": "max"}
    assert dominates({"t": 1, "s": 1}, {"t": 2, "s": 1}, axes)
    assert not dominates({"t": 1, "s": 0}, {"t": 2, "s": 1}, axes)


def test_selector_breaks_equal_axis_ties_deterministically(cfg_a):
    """Equal axis and cost values use itinerary ID as the final tie-breaker."""

    recommended = _test_itinerary(
        "recommended",
        total_time_min=120.0,
        detour_min=10.0,
        charge_min=20.0,
    )
    alphabetically_later = _test_itinerary(
        "zeta-fast",
        total_time_min=100.0,
        detour_min=10.0,
        charge_min=20.0,
    )
    alphabetically_earlier = _test_itinerary(
        "alpha-fast",
        total_time_min=100.0,
        detour_min=10.0,
        charge_min=20.0,
    )

    selector = TradeoffSelector(cfg_a["tradeoffs"])
    alternatives = selector.select(
        [
            (recommended, _test_cost(110.0)),
            (alphabetically_later, _test_cost(90.0)),
            (alphabetically_earlier, _test_cost(90.0)),
        ],
        _test_request(),
    )

    fastest = next(
        alternative for alternative in alternatives if alternative.label == "Fastest"
    )

    assert fastest.itinerary.itinerary_id == "alpha-fast"


def test_selector_suppresses_immaterial_detour_difference(cfg_a):
    """A tiny detour improvement should not create another driver option."""

    recommended = _test_itinerary(
        "recommended",
        total_time_min=100.0,
        detour_min=10.0,
        charge_min=20.0,
    )
    tiny_detour_improvement = _test_itinerary(
        "tiny-detour-change",
        total_time_min=100.0,
        detour_min=7.0,
        charge_min=20.0,
    )

    selector = TradeoffSelector(cfg_a["tradeoffs"])
    alternatives = selector.select(
        [
            (recommended, _test_cost(80.0)),
            (tiny_detour_improvement, _test_cost(85.0)),
        ],
        _test_request(),
    )

    assert [alternative.label for alternative in alternatives] == ["Recommended"]


def test_selector_never_returns_plan_dominated_by_recommendation(cfg_a):
    """A plan worse on every configured axis is not a valid alternative."""

    recommended = _test_itinerary(
        "recommended",
        total_time_min=100.0,
        detour_min=5.0,
        charge_min=10.0,
    )
    dominated_plan = _test_itinerary(
        "dominated",
        total_time_min=110.0,
        detour_min=8.0,
        charge_min=15.0,
    )

    selector = TradeoffSelector(cfg_a["tradeoffs"])
    alternatives = selector.select(
        [
            (recommended, _test_cost(80.0)),
            (dominated_plan, _test_cost(95.0)),
        ],
        _test_request(),
    )

    assert [alternative.itinerary.itinerary_id for alternative in alternatives] == [
        "recommended"
    ]


def test_selector_does_not_repeat_an_itinerary(cfg_a):
    """The same itinerary ID may appear only once in driver-facing output."""

    recommended = _test_itinerary(
        "same-itinerary",
        total_time_min=100.0,
        detour_min=5.0,
        charge_min=10.0,
    )
    duplicate_entry = recommended.model_copy(
        update={
            "total_drive_min": 80.0,
        }
    )

    selector = TradeoffSelector(cfg_a["tradeoffs"])
    alternatives = selector.select(
        [
            (recommended, _test_cost(90.0)),
            (duplicate_entry, _test_cost(80.0)),
        ],
        _test_request(),
    )

    selected_ids = [alternative.itinerary.itinerary_id for alternative in alternatives]

    assert selected_ids == ["same-itinerary"]
