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
whose search stopped early (configs/evaluation.yaml max_labels, or the pareto_time_budget_s safety net) gets an
`oracle_search_incomplete` finding, is counted in the `oracle_search` block, and is left out of
every front-based metric rather than judged against a partial front.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from clickless.contracts import Place, PlanResult, TripRequest

from ..oracle.energy_reference import is_charge_time_excluded
from ..oracle.label_setting import (
    CheckReport,
    FeasibilityResult,
    LabelSettingOracle,
    OracleUnavailable,
    ParetoResult,
    dominated_by_front,
    strictly_dominating,
)

# Codes that relaxation (configs/relaxation.yaml) never changes, so a relaxed plan must still pass them.
NEVER_RELAXED = frozenset(
    {"connector", "reserve", "status", "access", "power", "unknown_station", "departure_soc", "continuity"}
)
ENERGY_CODES = frozenset({"reserve", "arrival_soc"})

# Hard-constraint fields of a TripRequest compared between the gold and the parsed request
# (parse_changed_constraints finding). Places are left out: their text differs in formatting.
HARD_FIELDS = (
    "vehicle_id",
    "start_soc_pct",
    "arrival_soc_pct_min",
    "max_detour_min",
    "min_power_kw",
    "connectors_override",
)

# The relaxation step writes Relaxation.changes in these fixed forms (the contract carries no
# relaxed request). Anything else is reported as relaxation_unparseable, never accepted.
_NUM = r"(\d+(?:\.\d+)?)"
_DROP_AMENITY = re.compile(r"drop amenity: (\S+)")
_DETOUR = re.compile(rf"max detour {_NUM} -> {_NUM} min")
_ARRIVAL = re.compile(rf"arrival charge {_NUM}% -> {_NUM}%")


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


def relaxed_request(request: TripRequest, changes: Sequence[str]) -> tuple[TripRequest | None, str | None]:
    """Apply Relaxation.changes to the (gold) request: (relaxed request, None) or (None, why not)."""
    update: dict[str, Any] = {}
    amenities = list(request.amenities)
    for change in changes:
        text = change.strip()
        if m := _DROP_AMENITY.fullmatch(text):
            kept = [a for a in amenities if a.key != m.group(1)]
            if len(kept) == len(amenities):
                return None, f"change {change!r}: the request has no amenity {m.group(1)!r}"
            amenities = update["amenities"] = kept
        elif m := _DETOUR.fullmatch(text):
            update["max_detour_min"] = float(m.group(2))
        elif m := _ARRIVAL.fullmatch(text):
            update["arrival_soc_pct_min"] = float(m.group(2))
        else:
            return None, f"unparseable change {change!r}"
    return request.model_copy(update=update, deep=True), None


@dataclass
class ItemEval:
    item_id: str
    request: TripRequest
    result: PlanResult
    feasibility: FeasibilityResult
    reports: list[CheckReport] = field(default_factory=list)  # one per returned alternative
    relaxed_reports: list[CheckReport] = field(default_factory=list)  # relaxed plans vs the ORIGINAL request
    uncheckable: int = 0
    relaxed: TripRequest | None = None  # the request after Relaxation.changes
    relaxed_full_reports: list[CheckReport] = field(default_factory=list)  # relaxed plans vs `relaxed`
    relaxation_error: str | None = None  # Relaxation.changes could not be applied

    @property
    def recommended(self) -> CheckReport | None:
        return self.reports[0] if self.reports else None


def evaluate(
    items: Sequence, results: Sequence[PlanResult], oracle: LabelSettingOracle
) -> tuple[list[ItemEval], list[dict]]:
    """Run the oracle once per item. Returns evaluable items and the findings collected so far.

    Items left out (no gold request, no result, not evaluable) are listed by skipped_items()."""
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
        relaxed_alts = res.relaxation.alternatives if res.relaxation else []
        try:
            checked = oracle.check_plan_result(req, res)
        except OracleUnavailable as e:
            ev.uncheckable = len(res.alternatives) + len(relaxed_alts)
            findings.append({"item_id": item.id, "kind": "itinerary_uncheckable", "detail": str(e)})
        else:
            ev.reports, ev.relaxed_reports = checked["alternatives"], checked["relaxed"]
            if relaxed_alts:
                ev.relaxed, ev.relaxation_error = relaxed_request(req, res.relaxation.changes)
                if ev.relaxation_error:
                    findings.append(
                        {"item_id": item.id, "kind": "relaxation_unparseable", "detail": ev.relaxation_error}
                    )
                else:
                    ev.relaxed_full_reports = [oracle.check_itinerary(ev.relaxed, a.itinerary) for a in relaxed_alts]
        evals.append(ev)
    return evals, findings


def skipped_items(items: Sequence, results: Sequence[PlanResult], oracle: LabelSettingOracle) -> list[dict]:
    """Every item evaluate() leaves out, with the reason (oracle results are cached, so this is cheap)."""
    by_id = {r.request_id: r for r in results}
    out = []
    for item in items:
        req = request_from_item(item)
        if req is None:
            out.append({"item_id": item.id, "reason": "clarification_required", "detail": "no complete gold request"})
        elif item.id not in by_id:
            out.append({"item_id": item.id, "reason": "no_result", "detail": "the system returned no result"})
        else:
            fr = oracle.is_feasible(req)
            if not fr.evaluable:
                out.append({"item_id": item.id, "reason": fr.reason, "detail": fr.detail})
    return out


def _front_scope(evals: list[ItemEval]) -> list[ItemEval]:
    """Items the Pareto search runs on: oracle-feasible, and the planner returned a plan."""
    return [e for e in evals if e.feasibility.feasible and e.result.alternatives]


def _complete_front(oracle: LabelSettingOracle, request: TripRequest) -> ParetoResult | None:
    """The oracle front, or None when the search stopped early (excluded from front-based metrics)."""
    front = oracle.pareto_plans(request)
    return front if front.complete else None


def _incomplete_detail(oracle: LabelSettingOracle, front: ParetoResult) -> str:
    if front.incomplete_reason == "time_budget":
        why = f"wall-clock safety limit of {oracle.cfg.pareto_time_budget_s:g} s hit after {front.n_labels} labels"
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
        # Stopped by the wall-clock safety limit rather than max_labels: which items these are can
        # depend on machine load, so the run is not reproducible (oracle_nondeterministic_cutoff).
        "items_nondeterministic_cutoff": [i for i in incomplete if fronts[i].incomplete_reason == "time_budget"],
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
            if front.incomplete_reason == "time_budget":
                out.append(
                    {
                        "item_id": e.item_id,
                        "kind": "oracle_nondeterministic_cutoff",
                        "detail": f"the wall-clock safety limit ({oracle.cfg.pareto_time_budget_s:g} s) fired before "
                        f"max_labels ({oracle.cfg.max_labels}); whether this item is excluded depends on machine "
                        "load, so this run is not reproducible. Raise pareto_time_budget_s or lower max_labels.",
                    }
                )
    return out


def _same_value(a: Any, b: Any) -> bool:
    if isinstance(a, int | float) and isinstance(b, int | float):
        return math.isclose(float(a), float(b), abs_tol=1e-9)
    if isinstance(a, list) or isinstance(b, list):
        return sorted(getattr(c, "value", c) for c in a or []) == sorted(getattr(c, "value", c) for c in b or [])
    return a == b


def parse_differences(gold: TripRequest, parsed: TripRequest) -> dict[str, tuple[Any, Any]]:
    """Hard-constraint fields whose parsed value differs from the gold one: {field: (gold, parsed)}."""
    out = {}
    for f in HARD_FIELDS:
        g, p = getattr(gold, f), getattr(parsed, f)
        if not _same_value(g, p):
            norm = (lambda v: sorted(c.value for c in v) if isinstance(v, list) else v)  # noqa: E731
            out[f] = (norm(g), norm(p))
    return out


def _verifier_basis(ev: ItemEval, oracle: LabelSettingOracle) -> tuple[CheckReport | None, str]:
    """The oracle check to compare with the planner's verifier: on the PARSED request (what the
    verifier saw) when the result carries one the oracle can evaluate, else on the gold request."""
    res = ev.result
    if res.request is not None and res.alternatives:
        try:
            return oracle.check_itinerary(res.request, res.alternatives[0].itinerary), "parsed request"
        except OracleUnavailable:
            pass
    return ev.recommended, "gold request (parsed request not evaluable)" if res.request is not None else "gold request"


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
        # A plan that passes only within soc_tol_pct / detour_tol_min is its own finding: the strict
        # search cannot contain it, so it is not evidence of a planner/oracle disagreement.
        for k, r in enumerate(ev.reports):
            if r.ok and not r.strict_ok:
                out.append(
                    {
                        "item_id": ev.item_id,
                        "kind": "plan_passes_only_within_tolerance",
                        "detail": f"alternative {k} fails {r.strict_violations} without tolerance",
                    }
                )
        if not fr.feasible and any(r.ok and r.strict_ok for r in ev.reports):
            out.append(
                {
                    "item_id": ev.item_id,
                    "kind": "oracle_infeasible_plan_passes_check",
                    "detail": f"oracle reason {fr.reason}: {fr.detail}",
                }
            )
        if res.request is not None:
            diffs = parse_differences(ev.request, res.request)
            if diffs:
                out.append(
                    {
                        "item_id": ev.item_id,
                        "kind": "parse_changed_constraints",
                        "detail": "; ".join(f"{f}: gold {g!r}, parsed {p!r}" for f, (g, p) in diffs.items()),
                    }
                )
        if res.verification is not None and ev.recommended is not None:
            basis, on = _verifier_basis(ev, oracle)
            if basis is not None and res.verification.ok != basis.ok:
                out.append(
                    {
                        "item_id": ev.item_id,
                        "kind": "verifier_disagreement",
                        "detail": f"planner verifier ok={res.verification.ok}, oracle ok={basis.ok} on the {on}: "
                        f"{[v.code for v in basis.violations]}",
                    }
                )
        if fr.feasible and any(r.ok and r.strict_ok and r.objectives for r in ev.reports):
            front = _complete_front(oracle, ev.request)
            if front is None:  # reported once as oracle_search_incomplete (_incomplete_findings)
                continue
            for k, r in enumerate(ev.reports):
                if (
                    r.ok
                    and r.strict_ok
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


def _got_valid_plan(e: ItemEval) -> bool:
    """The system returned a plan and the oracle accepts its recommended itinerary (gold request)."""
    return e.result.status == "ok" and e.recommended is not None and e.recommended.ok


def per_item_outcomes(evals: list[ItemEval]) -> list[dict[str, Any]]:
    """One record per evaluable item, so the report can bootstrap over items and pair configs.

    `got_valid_plan` is what plan feasibility counts (over oracle-feasible items only);
    `n_itineraries` / `n_violating_itineraries` are what the hard-violation rate counts (every
    returned alternative, checked against the GOLD request).
    """
    return [
        {
            "item_id": e.item_id,
            "oracle_feasible": bool(e.feasibility.feasible),
            "got_valid_plan": _got_valid_plan(e),
            "n_itineraries": len(e.reports),
            "n_violating_itineraries": sum(1 for r in e.reports if not r.ok),
            "violation_codes": sorted({v.code for r in e.reports for v in r.violations}),
        }
        for e in evals
    ]


def feasibility_and_violations(items, results, oracle, evals: list[ItemEval] | None = None) -> dict[str, Any]:
    evals = evals if evals is not None else evaluate(items, results, oracle)[0]
    feasible = [e for e in evals if e.feasibility.feasible]
    got_valid = sum(1 for e in feasible if _got_valid_plan(e))
    rejected = sum(1 for e in feasible if e.result.status == "infeasible")
    reports = [r for e in evals for r in e.reports]
    codes = Counter(v.code for r in reports for v in r.violations)
    skipped = skipped_items(items, results, oracle)
    return {
        "n_items": len(items),
        "n_skipped": len(skipped),
        "skipped_by_reason": dict(sorted(Counter(s["reason"] for s in skipped).items())),
        "items_skipped": skipped,
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
        "items": per_item_outcomes(evals),
    }


def infeasible_handling(items, results, oracle, evals: list[ItemEval] | None = None) -> dict[str, Any]:
    evals = evals if evals is not None else evaluate(items, results, oracle)[0]
    infeasible = [e for e in evals if not e.feasibility.feasible]
    correct = sum(1 for e in infeasible if e.result.status == "infeasible" and not e.result.alternatives)
    offered = [e for e in infeasible if e.result.relaxation and e.result.relaxation.alternatives]
    # A relaxed plan is bad if it breaks a never-relaxed constraint of the ORIGINAL request, breaks
    # anything in the RELAXED request (e.g. detour 10 -> 15 but a 25-minute detour), or its
    # relaxation cannot be read (then nothing vouches for it).
    n_relaxed = bad_relaxed = 0
    for e in infeasible:
        for k, original in enumerate(e.relaxed_reports):
            n_relaxed += 1
            full = e.relaxed_full_reports[k] if k < len(e.relaxed_full_reports) else None
            bad_relaxed += (
                any(v.code in NEVER_RELAXED for v in original.violations)
                or e.relaxation_error is not None
                or (full is not None and not full.ok)
            )
    return {
        "n_oracle_infeasible": len(infeasible),
        "infeasible_handling_accuracy": _rate(correct, len(infeasible)),
        "relaxation_offered_rate": _rate(len(offered), len(infeasible)),
        "n_relaxed_itineraries": n_relaxed,
        "relaxed_plan_violation_rate": _rate(bad_relaxed, n_relaxed),
        "n_relaxation_unparseable": sum(1 for e in infeasible if e.relaxation_error is not None),
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
        # what the baseline run actually used: the oracle's override if set, else the charging model
        "baseline_dc_fast_kw": float(
            (oracle.cfg.unknown_power_kw or {}).get("dc_fast", oracle.cfg.charging_model["unknown_power_kw"]["dc_fast"])
        ),
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
    """Which oracle-infeasible items become feasible with a much wider corridor buffer?

    Feasibility can only gain from extra stations (more nodes never remove a path), so feasible
    items are not re-checked. The wide buffer is finite (configs/evaluation.yaml
    oracle.diagnostic_buffer_km): with no buffer every station becomes a node and the reach check
    is O(N^2) per item, which does not finish on a full snapshot."""
    evals = evals if evals is not None else evaluate(items, results, oracle)[0]
    wide_km = max(oracle.cfg.diagnostic_buffer_km, oracle.cfg.corridor_buffer_km)
    wide = oracle.with_overrides(corridor_buffer_km=wide_km)
    infeasible = [e for e in evals if not e.feasibility.feasible]
    changed = [e.item_id for e in infeasible if wide.is_feasible(e.request).feasible]
    return {
        "corridor_buffer_km": oracle.cfg.corridor_buffer_km,
        "diagnostic_buffer_km": wide_km,
        "n_rechecked": len(infeasible),
        "n_feasible_with_wide_buffer": len(changed),
        "items_feasible_with_wide_buffer": changed,
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
    if items and not evals:
        # Nothing to judge: say why instead of reporting None rates and no findings as if clean
        # (e.g. an empty routes/ makes every item not evaluable with reason no_route).
        why = Counter(s["reason"] for s in skipped_items(items, results, oracle))
        findings.append(
            {
                "item_id": None,
                "kind": "oracle_nothing_evaluable",
                "detail": f"0 of {len(items)} items evaluable: {dict(sorted(why.items()))}",
            }
        )
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
    return bool(strictly_dominating(point, plans, tol))


# --------------------------------------------------------------------------- nDCG


def dcg_at_k(relevances: Sequence[float], k: int = 3) -> float:
    """Discounted cumulative gain with graded relevance: sum over ranks i = 1..k of rel_i / log2(i + 1).

    Every rank after the first is discounted (rank 1: /1, rank 2: /1.585, rank 3: /2), so putting the
    most relevant plan first always scores higher. The original Jarvelin & Kekalainen (2002) form with
    b = 2 leaves ranks 1 and 2 undiscounted, which would score [0, 1] the same as [1, 0].
    """
    return sum(gain / math.log2(i + 1) for i, gain in enumerate(relevances[:k], start=1))


def ndcg_at_k_single(relevances: Sequence[float], k: int = 3) -> float:
    """nDCG@k of one ranked list. The ideal ranking sorts ALL judged relevances; NaN if its DCG is 0."""
    ideal = dcg_at_k(sorted(relevances, reverse=True), k)
    return math.nan if ideal == 0 else dcg_at_k(relevances, k) / ideal


def ndcg_at_k(ranked_relevances: list[list[float]], k: int = 3) -> float:
    """Mean nDCG@k over ranked lists, skipping lists whose ideal DCG is 0 (NaN if none remain)."""
    values = [v for v in (ndcg_at_k_single(r, k) for r in ranked_relevances) if not math.isnan(v)]
    return sum(values) / len(values) if values else math.nan
