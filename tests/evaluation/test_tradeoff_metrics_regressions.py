"""Regression coverage for trade-off metrics and sensitivity calculations.

Point 5 (partial fronts) was already fixed in e1efcbc and is covered by
test_tradeoff_metrics.py::test_pareto_validity_excludes_items_with_an_incomplete_search.
Point 14 (tests tied to the live config) is fixed by pinning TRADEOFFS in test_tradeoff_metrics.py.

The numbered sections preserve traceability to the original review findings,
while the filename remains meaningful after that review is closed.
"""

from __future__ import annotations

import pytest
import yaml
from _oracle_world import itinerary, port, station, stop, world
from test_tradeoff_metrics import D_ARRIVE, D_NEED, TRADEOFFS, alt, bench, pareto_world, plan, ranked

from clickless.config import repo_path
from clickless.contracts import AmenityCategory, AmenityPreference, Itinerary, PlanResult, TripRequest
from evaluation.metrics.planning import evaluate, request_from_item
from evaluation.metrics.tradeoffs import (
    amenity_credit_score,
    amenity_rule,
    charge_time_sensitivity,
    delta_faithfulness,
    pareto_validity,
    parse_claims,
    plan_cost,
    priority_phrase_accuracy,
    profile_weights,
    unknown_power_rank_sensitivity,
    weight_sensitivity,
)
from evaluation.oracle.label_setting import OracleUnavailable
from evaluation.schema import BenchmarkItem


def shown(request_id, itins, request=None):
    return PlanResult(
        request_id=request_id,
        config="A",
        status="ok",
        request=request,
        alternatives=[alt(i, "Recommended" if k == 0 else f"Alt{k}") for k, i in enumerate(itins)],
    )


# --------------------------------------------------------------------------- 1. buffer_only vs real domination


def test_p1_slow_port_at_the_strict_need_is_dominated_not_buffer_only():
    # Manas's case: M has CCS1 150 kW and 50 kW. The planner uses the 50 kW port and leaves at 76 %,
    # exactly what the oracle's strict plan does on the 150 kW port. The policy front (2 % buffer)
    # still charges far faster on 150 kW, so this is real domination, not the buffer.
    o = world([station("M", 60, ports=[port(kw=150), port(kw=50)])])
    m = pareto_validity([bench("m1")], [shown("m1", [itinerary(stop("M", kw=50, depart=76))])], o)
    assert (m["n_buffer_only"], m["n_dominated"]) == (0, 1)
    assert m["policy_adjusted_pareto_validity_rate"] == 0.0


def test_p1_buffer_only_still_detected_against_the_policy_front():
    o = pareto_world()
    m = pareto_validity([bench("m2")], [shown("m2", [itinerary(stop("S", arrive=26, depart=78))])], o)
    assert (m["n_buffer_only"], m["n_dominated"]) == (1, 0)
    assert m["not_valid"][0]["extra_charge_min"] == pytest.approx(1.2)


# --------------------------------------------------------------------------- 2. joined claims


def test_p2_joined_claims_are_split_and_total_detour_is_detour():
    claims = parse_claims("about 10 min quicker and about 4 min less charging (estimate)")
    assert [(c["key"], c["claimed"]) for c in claims] == [("total_time_min", -10.0), ("charge_min", -4.0)]
    assert [(c["key"], c["claimed"]) for c in parse_claims("about 3 min more total detour")] == [("detour_min", 3.0)]
    assert [(c["key"], c["claimed"]) for c in parse_claims("5 min faster but 2 min more detour")] == [
        ("total_time_min", -5.0),
        ("detour_min", 2.0),
    ]


# --------------------------------------------------------------------------- 3. units, unparsed


def test_p3_minute_and_hour_units():
    assert [(c["key"], c["claimed"]) for c in parse_claims("about 1 minute more detour")] == [("detour_min", 1.0)]
    assert [(c["key"], c["claimed"]) for c in parse_claims("about 1 hr slower")] == [("total_time_min", 60.0)]
    assert [(c["key"], c["claimed"]) for c in parse_claims("1.5 hours longer")] == [("total_time_min", 90.0)]


def test_p3_unreadable_claim_is_unparsed_not_faithful():
    assert parse_claims("a few minutes longer") == [
        {"text": "a few minutes longer", "key": None, "claimed": None, "unparsed": True}
    ]
    res = PlanResult(
        request_id="u",
        config="A",
        status="ok",
        explanation="Fastest: stop at A instead; a few minutes quicker; about 2 min more detour.",
        alternatives=[alt(itinerary()), alt(itinerary(), "Fastest", {"total_time_min": -12.0, "detour_min": 2.0})],
    )
    m = delta_faithfulness([res])
    assert (m["n_claims"], m["n_faithful"], m["n_unparsed"]) == (2, 1, 1)
    assert m["delta_faithfulness_rate"] == 0.5
    assert m["mismatches"][0]["reason"] == "unparsed"


# --------------------------------------------------------------------------- 4. oracle-infeasible plans


def test_p4_plan_on_an_oracle_infeasible_request_is_never_valid():
    # 140 km needs 56 %; start 75 %, no station: arrives with 19 % against a 19.4 % floor. The oracle
    # says infeasible; check_itinerary passes the direct plan within its 0.5 % SoC tolerance.
    o = world([], route_km=140.0)
    gold = bench("i1", start_soc_pct=75, arrival_soc_pct_min=19.4)
    m = pareto_validity([gold], [shown("i1", [itinerary()])], o)
    assert (m["n_valid"], m["n_oracle_infeasible_passes_check"]) == (0, 1)
    assert m["strict_pareto_validity_rate"] == 0.0


# --------------------------------------------------------------------------- 6. a null gold profile does not crash


def test_p6_null_gold_profile_is_its_own_row():
    item = BenchmarkItem(
        id="n1", author="ian", category="priority_tradeoff", route_type="rural", text="n1",
        gold_request={"origin": "a", "destination": "b", "vehicle_id": "t", "tradeoff_profile": None},
        clarification_required=True, missing_safety_fields=["start_soc_pct"],
    )
    items = [item, bench("q1", "priority_tradeoff", tradeoff_profile="fastest")]
    m = priority_phrase_accuracy(items, [ranked("n1", [plan(1, 1, 1)]), ranked("q1", [plan(1, 1, 1)])])
    assert m["confusion"] == {"fastest": {"balanced": 1}, "none": {"balanced": 1}}


# --------------------------------------------------------------------------- 7. flips against the plan shown first


def test_p7_flip_is_measured_against_the_plan_shown_first():
    # The ranker showed P1 first although this formula prefers P2 (163 vs 167, balanced): a mismatch,
    # reported as such and not as a perturbation flip.
    res = ranked("f1", [plan(141, 20, 2, sid="P1"), plan(145, 25, 6, food=1.0, sid="P2")], food=True)
    m = weight_sensitivity([bench("f1")], [res], TRADEOFFS)
    assert m["n_formula_ranker_mismatch"] == 1 and m["items_formula_ranker_mismatch"] == ["f1"]
    assert m["n_perturbations"] == 0 and m["flip_rate_pm20"] is None
    c = charge_time_sensitivity([bench("f1")], [res], TRADEOFFS)
    assert c["n_formula_ranker_mismatch"] == 1 and c["flip_rate_x0.75"] is None


# --------------------------------------------------------------------------- 8. amenity fallback = the ranker's


def test_p8_composite_amenity_key_uses_its_base_category_value():
    s = stop("P").model_copy(update={"satisfied_preferences": {"food_wheelchair": 1.0}})
    it = Itinerary(
        itinerary_id="x", request_id="r", vehicle_id="t", stops=[s], legs=[], total_drive_min=100,
        total_charge_min=10, total_detour_min=2, arrival_soc_pct=20,
    )
    request = TripRequest(request_id="r", raw_text="", amenities=[{"category": "food", "wheelchair_required": True}])
    # balanced: 100 + 10 + 0.5*2 + 5 - 15 (food's value; there is no food_wheelchair entry)
    assert plan_cost(it, request, profile_weights(TRADEOFFS, "balanced")) == pytest.approx(101.0)


def test_p8_fallback_in_evaluation_config_matches_the_ranker():
    """configs/evaluation.yaml copies the ranker's rule; this pins the two together for every amenity
    key and profile of the LIVE configs (the test may import ranking code; the metric may not)."""
    from clickless.ranking.cost import plan_cost as ranker_cost

    live = yaml.safe_load(repo_path("configs/tradeoffs.yaml").read_text(encoding="utf-8"))
    rule = amenity_rule()
    for category in AmenityCategory:
        for wheelchair in (False, True):
            pref = AmenityPreference(category=category, wheelchair_required=wheelchair)
            s = stop("P").model_copy(update={"satisfied_preferences": {pref.key: 1.0}})
            it = Itinerary(
                itinerary_id="x", request_id="r", vehicle_id="t", stops=[s], legs=[], total_drive_min=0,
                total_charge_min=0, total_detour_min=0, arrival_soc_pct=20,
            )
            for profile in live["profiles"]:
                request = TripRequest(request_id="r", raw_text="", amenities=[pref], tradeoff_profile=profile)
                ranker = ranker_cost(it, request, live, evidence_credit_min=0.0).amenity_credit_min
                weights = profile_weights(live, profile)
                ours = -(plan_cost(it, request, weights, rule=rule) - weights["stop_overhead_min"])
                assert ours == pytest.approx(ranker), (profile, pref.key)


@pytest.mark.parametrize(
    ("raw_score", "expected_credit_score"),
    [
        (0.0, 0.0),
        (0.10, 0.45),
        (0.45, 0.45),
        (0.80, 0.80),
    ],
)
def test_p8_config_a_credit_floor_matches_documented_policy(
    raw_score,
    expected_credit_score,
):
    """Evaluation independently reproduces Config A's positive-score floor."""

    config_a = yaml.safe_load(
        repo_path("configs/config_a.yaml").read_text(encoding="utf-8")
    )
    credit_policy = config_a["ranking"]["tfidf_credit"]

    assert amenity_credit_score(
        raw_score,
        credit_policy,
    ) == pytest.approx(expected_credit_score)


def test_p8_evaluation_cost_matches_ranker_with_credit_floor():
    """The independent evaluator and production ranker agree at their boundary."""

    from clickless.ranking.cost import plan_cost as ranker_cost

    config_a = yaml.safe_load(
        repo_path("configs/config_a.yaml").read_text(encoding="utf-8")
    )
    tradeoffs = yaml.safe_load(
        repo_path("configs/tradeoffs.yaml").read_text(encoding="utf-8")
    )
    credit_policy = config_a["ranking"]["tfidf_credit"]

    preference = AmenityPreference(category=AmenityCategory.FOOD)
    matched_stop = stop("P").model_copy(
        update={
            "satisfied_preferences": {
                preference.key: 0.20,
            }
        }
    )
    candidate = Itinerary(
        itinerary_id="floor-check",
        request_id="floor-check",
        vehicle_id="t",
        stops=[matched_stop],
        legs=[],
        total_drive_min=0,
        total_charge_min=0,
        total_detour_min=0,
        arrival_soc_pct=20,
    )
    request = TripRequest(
        request_id="floor-check",
        raw_text="",
        amenities=[preference],
        tradeoff_profile="balanced",
    )

    production_credit = ranker_cost(
        candidate,
        request,
        tradeoffs,
        amenity_credit_config=credit_policy,
    ).amenity_credit_min

    weights = profile_weights(tradeoffs, "balanced")
    evaluation_rule = {
        **amenity_rule(),
        "credit_policy": credit_policy,
    }
    evaluation_credit = -(
        plan_cost(
            candidate,
            request,
            weights,
            rule=evaluation_rule,
        )
        - weights["stop_overhead_min"]
    )

    expected_credit = (
        weights["amenity_value_min.food"]
        * credit_policy["floor"]
    )
    assert production_credit == pytest.approx(expected_credit)
    assert evaluation_credit == pytest.approx(expected_credit)


# --------------------------------------------------------------------------- 9. parsed preference keys


def test_p9_amenity_tradeoff_uses_the_parsed_keys():
    # Gold asks for an accessible restroom; the parser read "restroom". satisfied_preferences is keyed
    # by the parsed key, so the gold key would miss it and call an amenity trade-off "dominated".
    gold = bench("k1", amenities=[{"category": "restroom", "wheelchair_required": True}])
    parsed = request_from_item(gold).model_copy(update={"amenities": [AmenityPreference(category="restroom")]})
    d = stop("D", detour=6, arrive=D_ARRIVE, depart=D_NEED).model_copy(
        update={"satisfied_preferences": {"restroom": 1.0}}
    )
    m = pareto_validity([gold], [shown("k1", [itinerary(d)], request=parsed)], pareto_world())
    assert (m["n_amenity_tradeoff"], m["n_dominated"]) == (1, 0)
    assert m["items_parse_changed_amenities"] == ["k1"]
    assert m["not_valid"][0]["note"].startswith("parse_changed_amenities")


# --------------------------------------------------------------------------- 10. perturbations that cannot flip


def test_p10_duplicate_and_zero_weights_are_not_perturbed():
    # food and food_wheelchair both resolve to "food"; lodging is worth 0 in balanced.
    prefs = [{"category": "food"}, {"category": "food", "wheelchair_required": True}, {"category": "lodging"}]
    request = TripRequest(request_id="z1", raw_text="", vehicle_id="t", amenities=prefs)
    p2 = plan(145, 25, 6, food=1.0, sid="P2")
    res = PlanResult(
        request_id="z1", config="A", status="ok", request=request,
        alternatives=[alt(p2), alt(plan(141, 20, 2, sid="P1"), "Alt1")],
    )
    m = weight_sensitivity([bench("z1")], [res], TRADEOFFS)
    assert m["n_perturbations"] == (4 + 1) * 2  # 4 scalar weights + food, each x0.8 and x1.2
    assert m["n_perturbations_skipped_zero"] == 1  # lodging


# --------------------------------------------------------------------------- 11. unpriceable detail


def test_p11_oracle_unavailable_is_recorded_with_its_message():
    o = pareto_world()

    def unavailable(_request, _itinerary):
        raise OracleUnavailable("no_route: no route for a -> b")

    o.check_itinerary = unavailable
    m = pareto_validity([bench("x1")], [shown("x1", [itinerary(stop("S", depart=76))])], o)
    assert m["n_unpriceable"] == 1
    assert m["not_valid"] == [
        {"item_id": "x1", "label": "Recommended", "category": "unpriceable", "error": "no_route: no route for a -> b"}
    ]


# --------------------------------------------------------------------------- 12. unknown-power rank sensitivity


def test_p12_unknown_power_rank_sensitivity():
    # K (50 kW) and Q (unknown power) at km 60, both on the route. Q's claimed detour is 0.4 min longer
    # (+0.2 under balanced), so K is shown first and the base order is strict.
    # Under the oracle, Q charges 25 kWh at the 50 kW fallback (30 + 3 min). At 25 kW it takes 30 min
    # longer (K stays first); at 100 kW (capped at the car's 80) 11.25 min less, so Q overtakes K.
    o = world([station("K", 60, ports=[port(kw=50)]), station("Q", 60, ports=[port(kw=None)])])
    gold = bench("u1")
    plans = [itinerary(stop("K", kw=50, depart=76)), itinerary(stop("Q", kw=None, detour=2.4, depart=76))]
    res = shown("u1", plans,
                request=request_from_item(gold))
    m = unknown_power_rank_sensitivity([gold], [res], o, TRADEOFFS)
    assert m["n_items"] == 1 and m["baseline_dc_fast_kw"] == 50.0
    assert (m["flip_rate_25kw"], m["flip_rate_100kw"]) == (0.0, 1.0)
    assert m["kendall_tau_25kw"] == 1.0 and m["kendall_tau_100kw"] == -1.0


# --------------------------------------------------------------------------- 13. reuse the planning pass


def test_p13_pareto_validity_reuses_planning_evals():
    o = pareto_world()
    items, results = [bench("e1")], [shown("e1", [itinerary(stop("S", arrive=26, depart=76))])]
    evals, _ = evaluate(items, results, o)

    def must_not_run(*_a, **_k):
        raise AssertionError("check_itinerary re-run although ItemEval reports were given")

    o.check_itinerary = must_not_run
    m = pareto_validity(items, results, o, evals=evals)
    assert m["n_valid"] == 1
