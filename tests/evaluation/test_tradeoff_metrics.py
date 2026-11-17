"""Trade-off metrics with hand-computed answers.

Cost (docs/TRADEOFFS.md section 2, configs/tradeoffs.yaml):
  balanced = drive + charge + 0.5 detour + 5 stops - 15 food
  fastest  = drive + charge + 1.0 detour + 8 stops -  3 food
  comfort  = drive + 0.6 charge + 0.3 detour + 3 stops - 30 food
"""

import math

import pytest
import yaml
from _oracle_world import itinerary, off_for_detour, port, station, stop, world

from clickless.config import repo_path
from clickless.contracts import Alternative, Itinerary, PlanCost, PlanResult, TripRequest
from evaluation.metrics.tradeoffs import (
    charge_time_sensitivity,
    delta_faithfulness,
    kendall_tau_b,
    pareto_validity,
    parse_claims,
    plan_cost,
    priority_phrase_accuracy,
    profile_weights,
    tradeoff_metrics,
    weight_sensitivity,
)
from evaluation.schema import BenchmarkItem

TRADEOFFS = yaml.safe_load(repo_path("configs/tradeoffs.yaml").read_text(encoding="utf-8"))
COST = PlanCost(
    profile="balanced",
    drive_min=0,
    charge_min=0,
    detour_penalty_min=0,
    stop_overhead_min=0,
    amenity_credit_min=0,
    evidence_credit_min=0,
    total_min=0,
    amenity_satisfaction=1.0,
)


def bench(item_id, category="rural", **gold):
    g = {"origin": "a", "destination": "b", "vehicle_id": "t", "start_soc_pct": 50, **gold}
    return BenchmarkItem(
        id=item_id,
        author="ian",
        category=category,
        route_type="rural",
        text=item_id,
        gold_request=g,
        clarification_required=False,
    )


def alt(itin, label="Recommended", deltas=None):
    return Alternative(label=label, itinerary=itin, cost=COST, deltas=deltas or {})


def plan(drive, charge, detour, food=0.0, sid="P"):
    s = stop(sid).model_copy(update={"satisfied_preferences": {"food": food} if food else {}})
    return Itinerary(
        itinerary_id=sid,
        request_id="r",
        vehicle_id="t",
        stops=[s],
        legs=[],
        total_drive_min=drive,
        total_charge_min=charge,
        total_detour_min=detour,
        arrival_soc_pct=20,
    )


def ranked(request_id, plans, profile="balanced", food=False, vehicle_id="t"):
    req = TripRequest(
        request_id=request_id,
        raw_text="",
        vehicle_id=vehicle_id,
        tradeoff_profile=profile,
        amenities=[{"category": "food"}] if food else [],
    )
    return PlanResult(
        request_id=request_id,
        config="A",
        status="ok",
        request=req,
        alternatives=[alt(p, "Recommended" if k == 0 else f"Alt{k}") for k, p in enumerate(plans)],
    )


# --------------------------------------------------------------------------- Kendall tau, cost


def test_kendall_tau_b_hand_computed():
    assert kendall_tau_b([1, 2, 3, 4], [1, 3, 2, 4]) == pytest.approx(4 / 6)  # 5 concordant, 1 discordant
    assert kendall_tau_b([1, 1, 2], [1, 2, 3]) == pytest.approx(2 / math.sqrt(3 * 2))  # one tie in x
    assert kendall_tau_b([1, 2], [2, 1]) == -1.0
    assert math.isnan(kendall_tau_b([1, 1], [2, 2]))


def test_plan_cost_and_overrides():
    req = TripRequest(request_id="r", raw_text="", amenities=[{"category": "food"}])
    p2 = plan(145, 25, 6, food=1.0)
    assert plan_cost(p2, req, profile_weights(TRADEOFFS, "balanced")) == pytest.approx(163.0)
    assert plan_cost(p2, req, profile_weights(TRADEOFFS, "comfort")) == pytest.approx(134.8)
    w = profile_weights(TRADEOFFS, "balanced", {"amenity_value_min.food": 30, "bogus": 1})
    assert w["amenity_value_min.food"] == 30 and "bogus" not in w
    # an amenity the driver did not request never earns credit
    assert plan_cost(p2, req.model_copy(update={"amenities": []}), profile_weights(TRADEOFFS, "balanced")) == 178.0


# --------------------------------------------------------------------------- weight sensitivity

# P1 = (drive 141, charge 20, detour 2, 1 stop); P2 = (145, 25, 6, 1 stop, food 1.0)
# balanced 167 vs 163 (P2) | fastest 171 vs 181 (P1) | comfort 156.6 vs 134.8 (P2)
WEIGHT_RESULT = ranked("w1", [plan(141, 20, 2, sid="P1"), plan(145, 25, 6, food=1.0, sid="P2")], food=True)


def test_weight_sensitivity_across_profiles_and_pm20():
    m = weight_sensitivity([bench("w1")], [WEIGHT_RESULT], TRADEOFFS)
    assert m["scope"] == "displayed_plans_only" and m["n_items"] == 1
    assert m["kendall_tau_balanced_vs_fastest"] == -1.0 and m["flip_rate_balanced_vs_fastest"] == 1.0
    assert m["kendall_tau_balanced_vs_comfort"] == 1.0 and m["flip_rate_balanced_vs_comfort"] == 0.0
    assert m["kendall_tau_fastest_vs_comfort"] == -1.0 and m["flip_rate_fastest_vs_comfort"] == 1.0
    # 4 scalar weights + the requested food value, each x0.8 and x1.2; P2's margin survives every one
    assert m["n_perturbations"] == 10
    assert m["kendall_tau_pm20"] == 1.0 and m["flip_rate_pm20"] == 0.0 and m["item_flip_rate_pm20"] == 0.0


def test_pm20_flip_is_detected():
    # P1 167 vs P2' 166 under balanced: food x0.8 makes P2' 169 -> flip
    res = ranked("w2", [plan(141, 20, 2, sid="P1"), plan(145, 28, 6, food=1.0, sid="P2")], food=True)
    m = weight_sensitivity([bench("w2")], [res], TRADEOFFS)
    assert m["item_flip_rate_pm20"] == 1.0 and 0 < m["flip_rate_pm20"] < 1


def test_charge_time_sensitivity_excludes_leaf():
    # w1: x0.75 -> 162 vs 156.75, x1.25 -> 172 vs 169.25: no flip.
    # c2: P3 (150, 10, 2) 166 vs P4 (120, 44, 2) 170; x0.75 -> 163.5 vs 159: flip; x1.25 -> 168.5 vs 181.
    c2 = ranked("c2", [plan(150, 10, 2, sid="P3"), plan(120, 44, 2, sid="P4")])
    leaf = ranked("c3", [plan(150, 10, 2), plan(120, 44, 2)], vehicle_id="nissan-leaf-sv-plus-2024")
    m = charge_time_sensitivity([bench("w1"), bench("c2"), bench("c3")], [WEIGHT_RESULT, c2, leaf], TRADEOFFS)
    assert m["scope"] == "displayed_plans_only"
    assert m["n_items"] == 2 and m["n_leaf_excluded_from_charge_time"] == 1
    assert m["kendall_tau_x0.75"] == 0.0 and m["flip_rate_x0.75"] == 0.5
    assert m["kendall_tau_x1.25"] == 1.0 and m["flip_rate_x1.25"] == 0.0


def test_single_plan_results_are_not_ranked():
    one = ranked("s", [plan(100, 10, 2)])
    assert weight_sensitivity([bench("s")], [one], TRADEOFFS)["n_items"] == 0


def test_sensitivity_output_keys_name_the_displayed_plan_scope():
    m = tradeoff_metrics([], [], pareto_world(), TRADEOFFS)
    assert "displayed_plan_weight_sensitivity" in m and "displayed_plan_charge_time_sensitivity" in m
    assert "weight_sensitivity" not in m and "charge_time_sensitivity" not in m


# --------------------------------------------------------------------------- Pareto validity

# Oracle world (tests/evaluation/_oracle_world.py): route 200 km, start 50 %, stations at km 60.
# Front: F (148.15, 6, 22.15) and S (155, 2, 33). D (slow, 6 min detour) is dominated by both.
ACCESS = off_for_detour(6) * 1.3
D_ARRIVE, D_NEED = 50 - (60 + ACCESS) * 0.4, (ACCESS + 140) * 0.4 + 20


def pareto_world():
    return world(
        [
            station("F", 60, off_for_detour(6), [port(kw=150)]),
            station("S", 60, 0.0, [port(kw=50)]),
            station("D", 60, off_for_detour(6), [port(kw=50)]),
        ]
    )


def food_at(s):
    return s.model_copy(update={"satisfied_preferences": {"food": 1.0}})


def test_pareto_validity_without_amenities():
    shown = [
        itinerary(stop("S", arrive=26, depart=76)),  # on the front
        itinerary(stop("D", detour=6, arrive=D_ARRIVE, depart=D_NEED)),  # dominated by S and F
        itinerary(stop("S", arrive=26, depart=75)),  # arrives with 19 %
    ]
    res = PlanResult(request_id="p1", config="A", status="ok", alternatives=[alt(i) for i in shown])
    m = pareto_validity([bench("p1")], [res], pareto_world())
    assert (m["n_shown"], m["n_valid"], m["n_dominated"], m["n_infeasible"]) == (3, 1, 1, 1)
    assert m["n_buffer_only"] == 0
    assert m["strict_pareto_validity_rate"] == pytest.approx(1 / 3)
    assert m["policy_adjusted_pareto_validity_rate"] == pytest.approx(1 / 3)  # no buffer-only cases to drop
    dominated = next(d for d in m["not_valid"] if d["category"] == "dominated")
    assert sorted(dominated["dominated_by"]) == [["F"], ["S"]]


def test_pareto_validity_amenity_tradeoff_reported_separately():
    shown = [
        # food only at D, and no dominating plan visits D -> possibly better on amenities
        itinerary(food_at(stop("D", detour=6, arrive=D_ARRIVE, depart=D_NEED))),
        # food at S, but the front's S plan visits S with less charging -> dominated on all four axes
        itinerary(food_at(stop("S", arrive=26, depart=90))),
    ]
    res = PlanResult(request_id="p2", config="A", status="ok", alternatives=[alt(i) for i in shown])
    m = pareto_validity([bench("p2", amenities=[{"category": "food"}])], [res], pareto_world())
    assert (m["n_amenity_tradeoff"], m["n_dominated"], m["n_valid"]) == (1, 1, 0)
    assert m["strict_pareto_validity_rate"] == 0.0  # amenity trade-offs are not in the denominator


# The front's S plan departs S at exactly 76 % (what the next leg strictly needs); objectives
# (155.0, 2.0, 33.0). The planner adds charge_buffer_soc_pct = 2 (configs/energy.yaml) on top.
# Under the oracle's charging model, S departing at 78 % costs (156.2, 2.0, 34.2): same stations and
# detour, +1.2 charge minutes, which is more than pareto_tol_min = 0.5, so S strictly dominates it.


def test_pareto_validity_buffer_only_is_separate_from_dominated():
    shown = [
        itinerary(stop("S", arrive=26, depart=76)),  # on the front -> valid
        itinerary(stop("S", arrive=26, depart=78)),  # +2 %: exactly the buffer -> buffer_only
        itinerary(stop("S", arrive=26, depart=79)),  # +3 % > buffer + soc_tol (2.5) -> dominated
        itinerary(stop("D", detour=6, arrive=D_ARRIVE, depart=D_NEED)),  # other route -> dominated
        itinerary(stop("S", arrive=26, depart=75)),  # arrives with 19 % -> infeasible
    ]
    res = PlanResult(request_id="b1", config="A", status="ok", alternatives=[alt(i) for i in shown])
    m = pareto_validity([bench("b1")], [res], pareto_world())

    assert m["charge_buffer_soc_pct"] == 2.0  # read from the oracle's energy config
    counts = ("n_valid", "n_buffer_only", "n_dominated", "n_amenity_tradeoff", "n_infeasible", "n_unpriceable")
    assert tuple(m[k] for k in counts) == (1, 1, 2, 0, 1, 0)
    # strict: valid / (valid + buffer_only + dominated + infeasible) = 1 / 5
    assert m["strict_pareto_validity_rate"] == pytest.approx(1 / 5, rel=1e-6)
    # policy-adjusted: drops only the buffer_only case -> 1 / 4
    assert m["policy_adjusted_pareto_validity_rate"] == pytest.approx(1 / 4, rel=1e-6)

    buffered = next(d for d in m["not_valid"] if d["category"] == "buffer_only")
    assert buffered["dominated_by"] == [["S"]]
    assert buffered["buffer_baseline_objectives"] == [155.0, 2.0, 33.0]
    # extra charging time caused by the buffer: 34.2 - 33.0 = 1.2 min
    assert buffered["extra_charge_min"] == pytest.approx(1.2, rel=1e-6)
    assert m["buffer_only_extra_charge_min_total"] == pytest.approx(1.2, rel=1e-6)
    assert m["buffer_only_extra_charge_min_mean"] == pytest.approx(1.2, rel=1e-6)


def test_buffer_value_comes_from_energy_config():
    import dataclasses

    oracle = pareto_world()
    oracle.cfg = dataclasses.replace(oracle.cfg, energy={**oracle.cfg.energy, "charge_buffer_soc_pct": 0})
    res = PlanResult(
        request_id="b2",
        config="A",
        status="ok",
        alternatives=[alt(itinerary(stop("S", arrive=26, depart=78)))],
    )
    m = pareto_validity([bench("b2")], [res], oracle)
    # with no documented buffer, departing 2 % above the strict need is plain domination
    assert (m["charge_buffer_soc_pct"], m["n_buffer_only"], m["n_dominated"]) == (0.0, 0, 1)
    assert m["buffer_only_extra_charge_min_total"] == 0
    assert m["buffer_only_extra_charge_min_mean"] is None


# --------------------------------------------------------------------------- delta faithfulness


def test_parse_claims():
    assert parse_claims("about 25 minutes quicker") == [
        {"text": "25 minutes quicker", "key": "total_time_min", "claimed": -25.0}
    ]
    claims = parse_claims("+3 min detour; about 4 min less charging (estimate); 2 min walk")
    assert [(c["key"], c["claimed"]) for c in claims] == [("detour_min", 3.0), ("charge_min", -4.0), (None, 2.0)]
    assert parse_claims("stop at Station 7 instead") == []


def test_delta_faithfulness_per_claim():
    explanation = "\n".join(
        [
            "Stop at A ... about 18 min (estimate).",
            "Other options:",
            "  Fastest: stop at A instead; about 10 min quicker; about 3 min more detour; about 5 min walk.",
            "  Least detour: stop at B instead; about 25 min longer; about 4 min less charging (estimate).",
            "  Best amenities: drive directly instead; -10 min total; +0 min detour; -10 min charging.",
        ]
    )
    it = itinerary()
    res = PlanResult(
        request_id="d1",
        config="A",
        status="ok",
        explanation=explanation,
        alternatives=[
            alt(it),
            alt(it, "Fastest", {"total_time_min": -10.4, "detour_min": 3.0}),
            alt(it, "Least detour", {"total_time_min": 12.0, "charge_min": -4.2}),
            alt(it, "Best amenities", {"total_time_min": -10.0, "detour_min": 0.0}),
            alt(it, "Cheapest", {"total_time_min": 1.0}),  # no sentence in the text
        ],
    )
    m = delta_faithfulness([res])
    assert (m["n_alternatives"], m["n_alternatives_without_text"]) == (4, 1)
    assert (m["n_claims"], m["n_faithful"]) == (8, 5)
    assert m["delta_faithfulness_rate"] == pytest.approx(5 / 8)
    got = sorted((c["label"], c["reason"]) for c in m["mismatches"])
    assert got == [
        ("Best amenities", "no delta 'charge_min'"),
        ("Fastest", "unattributed number"),
        ("Least detour", "value differs"),
    ]
    bad = next(c for c in m["mismatches"] if c["reason"] == "value differs")
    assert (bad["claimed"], bad["actual"]) == (25.0, 12.0)


# --------------------------------------------------------------------------- priority phrases


def test_priority_phrase_accuracy():
    items = [
        bench("q1", "priority_tradeoff", tradeoff_profile="fastest"),
        bench("q2", "priority_tradeoff", tradeoff_profile="comfort"),
        bench("q3", "priority_tradeoff"),  # unstated -> balanced
        bench("q4", "priority_tradeoff", tradeoff_profile="fastest"),  # no result
        bench("q5", "rural", tradeoff_profile="fastest"),
    ]  # not a priority item
    results = [
        ranked("q1", [plan(1, 1, 1)], profile="fastest"),
        ranked("q2", [plan(1, 1, 1)]),
        ranked("q3", [plan(1, 1, 1)]),
        ranked("q5", [plan(1, 1, 1)]),
    ]
    m = priority_phrase_accuracy(items, results)
    assert (m["n_items"], m["priority_phrase_accuracy"], m["n_no_prediction"]) == (4, 0.5, 1)
    assert m["confusion"] == {
        "balanced": {"balanced": 1},
        "comfort": {"balanced": 1},
        "fastest": {"fastest": 1, "none": 1},
    }
