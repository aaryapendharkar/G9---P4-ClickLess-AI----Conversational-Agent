import pytest

from clickless.ranking.cost import plan_cost, resolve_profile
from clickless.ranking.tradeoffs import dominates


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
