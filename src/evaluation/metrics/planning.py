"""Planning-quality metrics (proposal section V-B). Owner: Ian.

  * plan feasibility rate (oracle-feasible items that got an oracle-valid itinerary; target >= 90 %)
  * hard-constraint violation rate over returned itineraries (target: zero observed)
  * infeasible items: correct rejection; relaxed plans keep the never-relaxed constraints
  * robust feasibility under +15 % consumption
  * unknown-power sensitivity (DC-fast fallback 25 and 100 kW instead of 50)
  * nDCG@3 against graded relevance labels (30 test items, 2 annotators)

The oracle judges the GOLD request of each benchmark item, so parser errors that lead to an invalid
plan count against the system. Every planner/oracle disagreement is returned as a finding
(`findings`); it is never resolved by changing the oracle to match the planner
(docs/EVALUATION_ORACLE.md). Must not import clickless.planning/ranking/retrieval/pipeline.

Front-based metrics (the oracle's Pareto search: front membership, oracle-fastest changes, unknown-
status reliance) run only on oracle-feasible items where the planner returned a plan. An item
whose search stopped early (configs/evaluation.yaml pareto_time_budget_s, or max_labels) gets an
`oracle_search_incomplete` finding, is counted in the `oracle_search` block, and is left out of
every front-based metric rather than judged against a partial front.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from clickless.contracts import Itinerary, Place, PlanResult, TripRequest

from ..oracle.energy_reference import is_charge_time_excluded
from ..oracle.label_setting import (
    CheckReport,
    FeasibilityResult,
    LabelSettingOracle,
    OracleUnavailable,
    ParetoResult,
    dominated_by_front,
)

# Codes that relaxation (configs/relaxation.yaml) never changes, so a relaxed plan must still pass them.
NEVER_RELAXED = frozenset(
    {"connector", "reserve", "status", "access", "power", "unknown_station", "departure_soc", "continuity"}
)
ENERGY_CODES = frozenset({"reserve", "arrival_soc"})


def _rate(num: int, den: int) -> float | None:
    return num / den if den else None


def request_from_item(item) -> TripRequest | None:
    """Gold TripRequest of a benchmark item; None when the gold request is incomplete (not evaluable)."""
    if getattr(item, "clarification_required", False) or getattr(item, "missing_safety_fields", None):
        return None
    gold = dict(item.gold_request)
    for key in ("origin", "destination"):
        if isinstance(gold.get(key), str):
            gold[key] = Place(text=gold[key])
    return TripRequest.model_validate({"request_id": item.id, "raw_text": item.text, **gold})


@dataclass
class ItemEval:
    item_id: str
    request: TripRequest
    result: PlanResult
    feasibility: FeasibilityResult
    reports: list[CheckReport] = field(default_factory=list)  # one per returned alternative
    relaxed_reports: list[CheckReport] = field(default_factory=list)
    uncheckable: int = 0

    @property
    def returned(self) -> list[Itinerary]:
        return [a.itinerary for a in self.result.alternatives]

    @property
    def recommended(self) -> CheckReport | None:
        return self.reports[0] if self.reports else None


def evaluate(
    items: Sequence, results: Sequence[PlanResult], oracle: LabelSettingOracle
) -> tuple[list[ItemEval], list[dict]]:
    """Run the oracle once per item. Returns evaluable items and the findings collected so far."""
    by_id = {r.request_id: r for r in results}
    evals, findings = [], []
    for item in items:
        req, res = request_from_item(item), by_id.get(item.id)
        if req is None or res is None:
            continue
        fr = oracle.is_feasible(req)
        if not fr.evaluable:
            continue
        ev = ItemEval(item.id, req, res, fr)
        for key, target in (("alternatives", ev.reports), ("relaxed", ev.relaxed_reports)):
            alts = (
                res.alternatives if key == "alternatives" else (res.relaxation.alternatives if res.relaxation else [])
            )
            for a in alts:
                try:
                    target.append(oracle.check_itinerary(req, a.itinerary))
                except OracleUnavailable as e:
                    ev.uncheckable += 1
                    findings.append({"item_id": item.id, "kind": "itinerary_uncheckable", "detail": str(e)})
        evals.append(ev)
    return evals, findings


def _front_scope(evals: list[ItemEval]) -> list[ItemEval]:
    """Items the Pareto search runs on: oracle-feasible, and the planner returned a plan."""
    return [e for e in evals if e.feasibility.feasible and e.result.alternatives]


def _complete_front(oracle: LabelSettingOracle, request: TripRequest) -> ParetoResult | None:
    """The oracle front, or None when the search stopped early (excluded from front-based metrics)."""
    front = oracle.pareto_plans(request)
    return front if front.complete else None


def _incomplete_detail(oracle: LabelSettingOracle, front: ParetoResult) -> str:
    if front.incomplete_reason == "time_budget":
        why = f"time budget of {oracle.cfg.pareto_time_budget_s:g} s hit"
    else:
        why = f"max_labels ({oracle.cfg.max_labels}) hit"
    return f"{why}; {len(front.plans)} plan(s) found; excluded from front-based metrics"


def oracle_search(items, results, oracle, evals: list[ItemEval] | None = None) -> dict[str, Any]:
    """How many items' Pareto searches stopped early (they are excluded from front-based metrics)."""
    evals = evals if evals is not None else evaluate(items, results, oracle)[0]
    scope = _front_scope(evals)
    fronts = {e.item_id: oracle.pareto_plans(e.request) for e in scope}
    incomplete = sorted(i for i, f in fronts.items() if not f.complete)
    return {
        "n_items_searched": len(scope),
        "n_incomplete": len(incomplete),
        "items_incomplete": incomplete,
        "incomplete_reasons": dict(sorted(Counter(fronts[i].incomplete_reason for i in incomplete).items())),
        "pareto_time_budget_s": oracle.cfg.pareto_time_budget_s,
        "max_labels": oracle.cfg.max_labels,
    }


def _incomplete_findings(evals: list[ItemEval], oracle: LabelSettingOracle) -> list[dict]:
    out = []
    for e in _front_scope(evals):
        front = oracle.pareto_plans(e.request)
        if not front.complete:
            out.append(
                {"item_id": e.item_id, "kind": "oracle_search_incomplete", "detail": _incomplete_detail(oracle, front)}
            )
    return out


def _findings(evals: list[ItemEval], oracle: LabelSettingOracle) -> list[dict]:
    out = []
    for ev in evals:
        fr, res = ev.feasibility, ev.result
        if fr.feasible and res.status == "infeasible":
            out.append(
                {
                    "item_id": ev.item_id,
                    "kind": "oracle_feasible_planner_rejected",
                    "detail": f"oracle witness {fr.witness}",
                }
            )
        if not fr.feasible and any(r.ok for r in ev.reports):
            out.append(
                {
                    "item_id": ev.item_id,
                    "kind": "oracle_infeasible_plan_passes_check",
                    "detail": f"oracle reason {fr.reason}: {fr.detail}",
                }
            )
        rec = ev.recommended
        if rec is not None and res.verification is not None and res.verification.ok != rec.ok:
            out.append(
                {
                    "item_id": ev.item_id,
                    "kind": "verifier_disagreement",
                    "detail": f"planner verifier ok={res.verification.ok}, oracle ok={rec.ok}: "
                    f"{[v.code for v in rec.violations]}",
                }
            )
        if fr.feasible and any(r.ok and r.objectives for r in ev.reports):
            front = _complete_front(oracle, ev.request)
            if front is None:  # reported once as oracle_search_incomplete (_incomplete_findings)
                continue
            for k, r in enumerate(ev.reports):
                if (
                    r.ok
                    and r.objectives
                    and not dominated_by_front(r.objectives, front.plans, oracle.cfg.pareto_tol_min)
                ):
                    out.append(
                        {
                            "item_id": ev.item_id,
                            "kind": "planner_plan_outside_oracle_front",
                            "detail": f"alternative {k} objectives {tuple(round(x, 2) for x in r.objectives)}",
                        }
                    )
    return out


# --------------------------------------------------------------------------- metric blocks


def feasibility_and_violations(items, results, oracle, evals: list[ItemEval] | None = None) -> dict[str, Any]:
    evals = evals if evals is not None else evaluate(items, results, oracle)[0]
    feasible = [e for e in evals if e.feasibility.feasible]
    got_valid = sum(1 for e in feasible if e.result.status == "ok" and e.recommended is not None and e.recommended.ok)
    rejected = sum(1 for e in feasible if e.result.status == "infeasible")
    reports = [r for e in evals for r in e.reports]
    codes = Counter(v.code for r in reports for v in r.violations)
    return {
        "n_evaluable": len(evals),
        "n_oracle_feasible": len(feasible),
        "n_oracle_infeasible": len(evals) - len(feasible),
        "plan_feasibility_rate": _rate(got_valid, len(feasible)),
        "false_rejection_rate": _rate(rejected, len(feasible)),
        "n_itineraries": len(reports),
        "hard_violation_rate": _rate(sum(1 for r in reports if not r.ok), len(reports)),
        "violations_by_code": dict(sorted(codes.items())),
        "n_uncheckable_itineraries": sum(e.uncheckable for e in evals),
        "n_leaf_feasibility_items": sum(1 for e in evals if e.feasibility.charge_time_excluded),
    }


def infeasible_handling(items, results, oracle, evals: list[ItemEval] | None = None) -> dict[str, Any]:
    evals = evals if evals is not None else evaluate(items, results, oracle)[0]
    infeasible = [e for e in evals if not e.feasibility.feasible]
    correct = sum(1 for e in infeasible if e.result.status == "infeasible" and not e.result.alternatives)
    offered = [e for e in infeasible if e.result.relaxation and e.result.relaxation.alternatives]
    relaxed = [r for e in infeasible for r in e.relaxed_reports]
    bad_relaxed = sum(1 for r in relaxed if any(v.code in NEVER_RELAXED for v in r.violations))
    return {
        "n_oracle_infeasible": len(infeasible),
        "infeasible_handling_accuracy": _rate(correct, len(infeasible)),
        "relaxation_offered_rate": _rate(len(offered), len(infeasible)),
        "n_relaxed_itineraries": len(relaxed),
        "relaxed_plan_violation_rate": _rate(bad_relaxed, len(relaxed)),
        "oracle_reasons": dict(sorted(Counter(e.feasibility.reason for e in infeasible).items())),
    }


def robust_feasibility(items, results, oracle, evals: list[ItemEval] | None = None) -> dict[str, Any]:
    evals = evals if evals is not None else evaluate(items, results, oracle)[0]
    penalty = float(oracle.cfg.energy["efficiency_penalty_robust"])
    robust = oracle.with_overrides(efficiency_penalty=penalty)
    valid = [e for e in evals if e.recommended is not None and e.recommended.ok]
    survive = 0
    for e in valid:
        rep = robust.check_itinerary(e.request, e.result.alternatives[0].itinerary)
        survive += not any(v.code in ENERGY_CODES for v in rep.violations)
    feasible = [e for e in evals if e.feasibility.feasible]
    still = sum(1 for e in feasible if robust.is_feasible(e.request).feasible)
    return {
        "efficiency_penalty": penalty,
        "n_valid_recommended": len(valid),
        "robust_plan_rate": _rate(survive, len(valid)),
        "robust_oracle_feasible_rate": _rate(still, len(feasible)),
    }


def unknown_power_sensitivity(
    items, results, oracle, fallbacks: Sequence[float] = (25.0, 100.0), evals: list[ItemEval] | None = None
) -> dict[str, Any]:
    """Vary only the DC-fast fallback (ADR 0010); the LEAF is excluded (ADR 0009)."""
    evals = evals if evals is not None else evaluate(items, results, oracle)[0]
    in_scope, leaf = [], 0
    for e in evals:
        if e.recommended is None or not e.recommended.ok or not e.recommended.uses_unknown_power:
            continue
        if is_charge_time_excluded(e.request.vehicle_id):
            leaf += 1
            continue
        in_scope.append(e)
    # Front-based parts: planner returned a plan, oracle-feasible, and the baseline search completed.
    base_fronts = {
        e.item_id: _complete_front(oracle, e.request)
        for e in _front_scope(evals)
        if not is_charge_time_excluded(e.request.vehicle_id)
    }
    base_fastest = {i: _fastest_of(f) for i, f in base_fronts.items() if f is not None}
    out: dict[str, Any] = {
        "n_items": len(in_scope),
        "n_leaf_excluded_from_charge_time": leaf,
        "baseline_dc_fast_kw": float(oracle.cfg.charging_model["unknown_power_kw"]["dc_fast"]),
        "n_excluded_incomplete_search": sum(f is None for f in base_fronts.values()),
    }
    for kw in fallbacks:
        alt = oracle.with_overrides(unknown_power_kw={**(oracle.cfg.unknown_power_kw or {}), "dc_fast": float(kw)})
        d_charge, on_front, on_front_n, incomplete = [], 0, 0, 0
        for e in in_scope:
            itin = e.result.alternatives[0].itinerary
            base, new = e.recommended.objectives, alt.check_itinerary(e.request, itin).objectives
            if base is None or new is None:
                continue
            d_charge.append(new[2] - base[2])
            if base_fronts.get(e.item_id) is None:
                continue
            alt_front = _complete_front(alt, e.request)
            if alt_front is None:
                incomplete += 1
                continue
            on_front_n += 1
            on_front += not _strictly_beaten(new, alt_front.plans, oracle.cfg.pareto_tol_min)
        changed = n_changed = 0
        for item_id, fastest in base_fastest.items():
            alt_front = _complete_front(alt, next(e.request for e in evals if e.item_id == item_id))
            if alt_front is None:
                incomplete += 1
                continue
            n_changed += 1
            changed += _fastest_of(alt_front) != fastest
        tag = f"{kw:g}kw"
        out[f"mean_delta_charge_min_{tag}"] = sum(d_charge) / len(d_charge) if d_charge else None
        out[f"max_abs_delta_charge_min_{tag}"] = max((abs(d) for d in d_charge), default=None)
        out[f"recommended_on_front_rate_{tag}"] = _rate(on_front, on_front_n)
        out[f"oracle_fastest_change_rate_{tag}"] = _rate(changed, n_changed)
        out[f"n_excluded_incomplete_search_{tag}"] = incomplete
    return out


def corridor_buffer_diagnostic(items, results, oracle, evals: list[ItemEval] | None = None) -> dict[str, Any]:
    evals = evals if evals is not None else evaluate(items, results, oracle)[0]
    unbounded = oracle.with_overrides(corridor_buffer_km=float("inf"))
    changed = [e.item_id for e in evals if unbounded.is_feasible(e.request).feasible != e.feasibility.feasible]
    return {
        "corridor_buffer_km": oracle.cfg.corridor_buffer_km,
        "n_feasibility_changes_without_buffer": len(changed),
        "items_changed_without_buffer": changed,
    }


def unknown_status_reliance(items, results, oracle, evals: list[ItemEval] | None = None) -> dict[str, Any]:
    """How many oracle plans rely on a station whose status is UNKNOWN (gap 9)."""
    evals = evals if evals is not None else evaluate(items, results, oracle)[0]
    plans = all_unknown = items_any = 0
    n_plans = 0
    fronts = [(e, _complete_front(oracle, e.request)) for e in _front_scope(evals)]
    feasible = [e for e, f in fronts if f is not None]
    for _e, front_result in fronts:
        if front_result is None:
            continue
        front = front_result.plans
        flags = [bool(p.unknown_status_station_ids) for p in front]
        n_plans += len(front)
        plans += sum(flags)
        items_any += any(flags)
        all_unknown += bool(flags) and all(flags)
    return {
        "n_oracle_plans": n_plans,
        "oracle_plans_unknown_status": plans,
        "oracle_plans_unknown_status_share": _rate(plans, n_plans),
        "items_any_plan_unknown_status_share": _rate(items_any, len(feasible)),
        "items_all_plans_unknown_status_share": _rate(all_unknown, len(feasible)),
        "n_excluded_incomplete_search": sum(f is None for _e, f in fronts),
    }


def planning_metrics(items, results, oracle) -> dict[str, Any]:
    """All planning blocks plus `findings` (planner/oracle disagreements) from one oracle pass."""
    evals, findings = evaluate(items, results, oracle)
    return {
        "feasibility": feasibility_and_violations(items, results, oracle, evals),
        "infeasible": infeasible_handling(items, results, oracle, evals),
        "robust": robust_feasibility(items, results, oracle, evals),
        "unknown_power_sensitivity": unknown_power_sensitivity(items, results, oracle, evals=evals),
        "corridor_buffer": corridor_buffer_diagnostic(items, results, oracle, evals),
        "unknown_status": unknown_status_reliance(items, results, oracle, evals),
        "oracle_search": oracle_search(items, results, oracle, evals),
        "findings": findings + _incomplete_findings(evals, oracle) + _findings(evals, oracle),
    }


def _fastest(oracle: LabelSettingOracle, request: TripRequest) -> tuple[str, ...] | None:
    return _fastest_of(oracle.pareto_plans(request))


def _fastest_of(front: ParetoResult) -> tuple[str, ...] | None:
    plans = front.plans
    return min(plans, key=lambda p: p.total_time_min).station_ids if plans else None


def _strictly_beaten(point, plans, tol: float) -> bool:
    """Some oracle plan is better by more than tol on one objective and no worse on the others."""
    def beats(objectives) -> bool:
        pairs = list(zip(objectives, point, strict=True))
        return all(a <= b + tol for a, b in pairs) and any(a < b - tol for a, b in pairs)

    return any(beats(p.objectives) for p in plans)


def ndcg_at_k(ranked_relevances: list[list[float]], k: int = 3) -> float:
    raise NotImplementedError("TODO(Ian): nDCG@k (Jarvelin & Kekalainen)")
