"""Trade-off metrics (TA feedback). Owner: Ian. See docs/TRADEOFFS.md section 5.

  * pareto_validity: shown alternatives must be non-dominated among the ORACLE's feasible plans on
    (total time, detour, charge time), within the oracle's pareto_tol_min. An alternative dominated
    on those three but possibly better on amenities is reported separately, not counted invalid
    (docs/EVALUATION_ORACLE.md gap 12).
  * delta_faithfulness: every number in an alternative's sentence must equal the matching
    Alternative.deltas value (up to the presenter's whole-minute rounding); mismatches per claim.
  * weight_sensitivity: Kendall tau-b of plan rankings across the three profiles and under +-20 %
    weights, plus the Recommended-flip rate.
  * charge_time_sensitivity: the same with charge minutes x0.75 / x1.25 (LEAF excluded, ADR 0009).
  * priority_phrase_accuracy: predicted vs gold tradeoff_profile on priority_tradeoff items.

Rankings use the cost formula of docs/TRADEOFFS.md section 2 with configs/tradeoffs.yaml, written
here from the documentation -- never ranking code. Like the oracle, this module must not import
clickless.planning, ranking, retrieval or pipeline (tests/evaluation/test_oracle_independence.py).
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from itertools import combinations
from typing import Any

from clickless.contracts import Itinerary, PlanResult, TripRequest

from ..oracle.energy_reference import is_charge_time_excluded
from ..oracle.label_setting import LabelSettingOracle, OraclePlan, OracleUnavailable, strictly_dominating
from .planning import request_from_item

PROFILES = ("balanced", "fastest", "comfort")
WEIGHT_FACTORS = (0.8, 1.2)
CHARGE_FACTORS = (0.75, 1.25)
SCALAR_WEIGHTS = ("drive_min_weight", "charge_min_weight", "detour_extra_weight", "stop_overhead_min")


def _rate(num: int, den: int) -> float | None:
    return num / den if den else None


def _mean(values: Sequence[float]) -> float | None:
    vals = [v for v in values if not math.isnan(v)]
    return sum(vals) / len(vals) if vals else None


# --------------------------------------------------------------------------- Pareto validity


def _amenity_tradeoff(request: TripRequest, itin: Itinerary, dominating: list[OraclePlan]) -> bool:
    """Could the dominated alternative still be better on amenities than every plan dominating it?

    Amenity satisfaction is the best match per requested preference over a plan's stops, so a plan
    that visits every station where the alternative satisfies a preference is at least as good on
    amenities. Only when no dominating plan does that can the alternative be an amenity trade-off.
    """
    keys = {p.key for p in request.amenities}
    satisfying = {s.station_id for s in itin.stops if any(s.satisfied_preferences.get(k, 0.0) > 0 for k in keys)}
    if not satisfying:
        return False
    return not any(satisfying <= set(p.station_ids) for p in dominating)


def pareto_validity(items, results: Sequence[PlanResult], oracle: LabelSettingOracle) -> dict[str, Any]:
    by_id = {r.request_id: r for r in results}
    tol = oracle.cfg.pareto_tol_min
    counts: Counter[str] = Counter()
    details: list[dict[str, Any]] = []
    for item in items:
        req, res = request_from_item(item), by_id.get(item.id)
        if req is None or res is None or res.status != "ok" or not res.alternatives:
            continue
        fr = oracle.is_feasible(req)
        if not fr.evaluable:
            continue
        front = oracle.pareto_plans(req).plans if fr.feasible else []
        for alt in res.alternatives:
            try:
                rep = oracle.check_itinerary(req, alt.itinerary)
            except OracleUnavailable:
                counts["unpriceable"] += 1
                continue
            dominating: list[OraclePlan] = []
            if not rep.ok:
                category = "infeasible"
            elif rep.objectives is None:
                category = "unpriceable"
            else:
                dominating = strictly_dominating(rep.objectives, front, tol)
                if not dominating:
                    category = "valid"
                elif _amenity_tradeoff(req, alt.itinerary, dominating):
                    category = "amenity_tradeoff"
                else:
                    category = "dominated"
            counts[category] += 1
            if category != "valid":
                details.append(
                    {
                        "item_id": item.id,
                        "label": alt.label,
                        "category": category,
                        "objectives": None if rep.objectives is None else [round(x, 2) for x in rep.objectives],
                        "dominated_by": [list(p.station_ids) for p in dominating],
                        "violations": [v.code for v in rep.violations],
                    }
                )
    judged = counts["valid"] + counts["dominated"] + counts["infeasible"]
    return {
        "n_shown": sum(counts.values()),
        "n_valid": counts["valid"],
        "n_dominated": counts["dominated"],
        "n_infeasible": counts["infeasible"],
        "n_amenity_tradeoff": counts["amenity_tradeoff"],  # reported separately, not counted invalid
        "n_unpriceable": counts["unpriceable"],
        "pareto_validity_rate": _rate(counts["valid"], judged),
        "tolerance_min": tol,
        "not_valid": details,
    }


# --------------------------------------------------------------------------- delta faithfulness

_CLAIM = re.compile(
    r"(?P<sign>[+\-−])?\s*(?P<num>\d+(?:\.\d+)?)\s*(?:minutes|mins|min)\b(?P<rest>[^;,.()\n]*)", re.IGNORECASE
)
# (phrases, delta key, direction). Longer phrases first so "less detour" wins over "detour".
_KEYWORDS: tuple[tuple[tuple[str, ...], str, int], ...] = (
    (("less detour", "fewer detour"), "detour_min", -1),
    (("more detour", "extra detour"), "detour_min", +1),
    (("less charging", "less charge", "shorter charging"), "charge_min", -1),
    (("more charging", "more charge", "longer charging"), "charge_min", +1),
    (("quicker", "faster", "shorter", "sooner", "less total", "saved"), "total_time_min", -1),
    (("longer", "slower", "more total", "later"), "total_time_min", +1),
    (("detour",), "detour_min", +1),
    (("charging", "charge"), "charge_min", +1),
    (("total",), "total_time_min", +1),
)


def parse_claims(text: str) -> list[dict[str, Any]]:
    """Numbers stated in minutes, each mapped to (delta key, signed value); key None if unattributable."""
    claims = []
    for m in _CLAIM.finditer(text):
        rest = m.group("rest").strip().lower()
        key, direction = None, 1
        for phrases, k, d in _KEYWORDS:
            if any(rest.startswith(p) or f" {p}" in f" {rest}" for p in phrases):
                key, direction = k, d
                break
        sign = -1 if m.group("sign") in ("-", "−") else 1
        claims.append({"text": m.group(0).strip(), "key": key, "claimed": sign * direction * float(m.group("num"))})
    return claims


def alternative_text(explanation: str, label: str) -> str | None:
    """The explanation line(s) describing one alternative ("<label>: ...")."""
    lines = [ln.strip() for ln in explanation.splitlines() if ln.strip().startswith(f"{label}:")]
    return " ".join(lines) if lines else None


def delta_faithfulness(results: Sequence[PlanResult], rounding_tol: float = 0.5) -> dict[str, Any]:
    """rounding_tol 0.5: the presenter prints whole minutes, so |claimed - delta| <= 0.5 is faithful."""
    n_alts = missing = 0
    claims: list[dict[str, Any]] = []
    for res in results:
        for alt in res.alternatives[1:]:  # alternatives after Recommended carry deltas
            n_alts += 1
            text = alternative_text(res.explanation, alt.label)
            if text is None:
                missing += 1
                continue
            for c in parse_claims(text):
                actual = alt.deltas.get(c["key"]) if c["key"] else None
                if c["key"] is None:
                    reason = "unattributed number"
                elif actual is None:
                    reason = f"no delta {c['key']!r}"
                elif abs(c["claimed"] - actual) > rounding_tol:
                    reason = "value differs"
                else:
                    reason = None
                claims.append(
                    {
                        "request_id": res.request_id,
                        "label": alt.label,
                        **c,
                        "actual": actual,
                        "ok": reason is None,
                        "reason": reason,
                    }
                )
    ok = sum(c["ok"] for c in claims)
    return {
        "n_alternatives": n_alts,
        "n_alternatives_without_text": missing,
        "n_claims": len(claims),
        "n_faithful": ok,
        "delta_faithfulness_rate": _rate(ok, len(claims)),
        "rounding_tol_min": rounding_tol,
        "mismatches": [c for c in claims if not c["ok"]],
    }


# --------------------------------------------------------------------------- cost model (TRADEOFFS section 2)


def profile_weights(tradeoffs_cfg: Mapping[str, Any], profile: str, overrides: Mapping[str, float] | None = None):
    """Flat weights: the profile's scalar terms plus amenity_value_min.<key>, with known overrides applied."""
    p = tradeoffs_cfg["profiles"][profile]
    w = {k: float(p[k]) for k in SCALAR_WEIGHTS}
    w.update({f"amenity_value_min.{k}": float(v) for k, v in p["amenity_value_min"].items()})
    for k, v in (overrides or {}).items():
        if k in w or k.startswith("amenity_value_min."):
            w[k] = float(v)
    return w


def plan_cost(
    itin: Itinerary,
    request: TripRequest,
    weights: Mapping[str, float],
    evidence_credit_min: float = 0.0,
    charge_scale: float = 1.0,
) -> float:
    """cost = w_drive*drive + w_charge*charge + detour_extra*detour + overhead*stops
    - sum over REQUESTED preferences of amenity_value_min[p] * best match score at any stop - evidence."""
    cost = (
        weights["drive_min_weight"] * itin.total_drive_min
        + weights["charge_min_weight"] * itin.total_charge_min * charge_scale
        + weights["detour_extra_weight"] * itin.total_detour_min
        + weights["stop_overhead_min"] * len(itin.stops)
    )
    for key in {p.key for p in request.amenities}:  # once per trip, at its best stop
        best = max((s.satisfied_preferences.get(key, 0.0) for s in itin.stops), default=0.0)
        cost -= weights.get(f"amenity_value_min.{key}", 0.0) * best
    return cost - evidence_credit_min


def kendall_tau_b(x: Sequence[float], y: Sequence[float]) -> float:
    """Kendall tau-b (ties handled). NaN when either side has no untied pair."""
    conc = disc = tie_x = tie_y = 0
    for i, j in combinations(range(len(x)), 2):
        dx = (x[i] > x[j]) - (x[i] < x[j])
        dy = (y[i] > y[j]) - (y[i] < y[j])
        if dx == 0 and dy == 0:
            continue
        if dx == 0:
            tie_x += 1
        elif dy == 0:
            tie_y += 1
        elif dx == dy:
            conc += 1
        else:
            disc += 1
    denom = math.sqrt((conc + disc + tie_x) * (conc + disc + tie_y))
    return math.nan if denom == 0 else (conc - disc) / denom


def _top(costs: Sequence[float]) -> int:
    return min(range(len(costs)), key=lambda i: (costs[i], i))


def _cases(items, results: Sequence[PlanResult]):
    """(item id, request, itineraries, evidence credits) for results with >= 2 shown plans."""
    ids = {i.id for i in items}
    for res in results:
        if res.request_id in ids and res.request is not None and len(res.alternatives) >= 2:
            yield (
                res.request_id,
                res.request,
                [a.itinerary for a in res.alternatives],
                [a.cost.evidence_credit_min for a in res.alternatives],
            )


def _costs(req, itins, evidence, weights, charge_scale=1.0) -> list[float]:
    return [plan_cost(it, req, weights, ev, charge_scale) for it, ev in zip(itins, evidence, strict=True)]


def weight_sensitivity(items, results: Sequence[PlanResult], tradeoffs_cfg: Mapping[str, Any]) -> dict[str, Any]:
    profile_tau: dict[str, list[float]] = defaultdict(list)
    profile_flip: dict[str, list[int]] = defaultdict(list)
    pert_tau, pert_flip, item_flip = [], [], []
    n = 0
    for _id, req, itins, ev in _cases(items, results):
        n += 1
        by_profile = {
            p: _costs(req, itins, ev, profile_weights(tradeoffs_cfg, p, req.weight_overrides)) for p in PROFILES
        }
        for a, b in combinations(PROFILES, 2):
            profile_tau[f"{a}_vs_{b}"].append(kendall_tau_b(by_profile[a], by_profile[b]))
            profile_flip[f"{a}_vs_{b}"].append(int(_top(by_profile[a]) != _top(by_profile[b])))
        base_w = profile_weights(tradeoffs_cfg, req.tradeoff_profile, req.weight_overrides)
        base = _costs(req, itins, ev, base_w)
        keys = [*SCALAR_WEIGHTS, *sorted(f"amenity_value_min.{p.key}" for p in req.amenities)]
        flipped = False
        for key in keys:
            for f in WEIGHT_FACTORS:
                costs = _costs(req, itins, ev, {**base_w, key: base_w.get(key, 0.0) * f})
                pert_tau.append(kendall_tau_b(base, costs))
                flip = _top(costs) != _top(base)
                pert_flip.append(int(flip))
                flipped |= flip
        item_flip.append(int(flipped))
    out: dict[str, Any] = {"n_items": n}
    for pair in profile_tau:
        out[f"kendall_tau_{pair}"] = _mean(profile_tau[pair])
        out[f"flip_rate_{pair}"] = _rate(sum(profile_flip[pair]), len(profile_flip[pair]))
    out["kendall_tau_pm20"] = _mean(pert_tau)
    out["flip_rate_pm20"] = _rate(sum(pert_flip), len(pert_flip))  # over (item, perturbation)
    out["item_flip_rate_pm20"] = _rate(sum(item_flip), len(item_flip))  # any perturbation flips the item
    out["n_perturbations"] = len(pert_flip)
    return out


def charge_time_sensitivity(
    items, results: Sequence[PlanResult], tradeoffs_cfg: Mapping[str, Any], factors: Sequence[float] = CHARGE_FACTORS
) -> dict[str, Any]:
    taus: dict[float, list[float]] = defaultdict(list)
    flips: dict[float, list[int]] = defaultdict(list)
    n = leaf = 0
    for _id, req, itins, ev in _cases(items, results):
        if is_charge_time_excluded(req.vehicle_id):
            leaf += 1
            continue
        n += 1
        w = profile_weights(tradeoffs_cfg, req.tradeoff_profile, req.weight_overrides)
        base = _costs(req, itins, ev, w)
        for f in factors:
            costs = _costs(req, itins, ev, w, charge_scale=f)
            taus[f].append(kendall_tau_b(base, costs))
            flips[f].append(int(_top(costs) != _top(base)))
    out: dict[str, Any] = {"n_items": n, "n_leaf_excluded_from_charge_time": leaf}
    for f in factors:
        out[f"kendall_tau_x{f:g}"] = _mean(taus[f])
        out[f"flip_rate_x{f:g}"] = _rate(sum(flips[f]), len(flips[f]))
    return out


# --------------------------------------------------------------------------- priority phrases


def priority_phrase_accuracy(items, results: Sequence[PlanResult]) -> dict[str, Any]:
    by_id = {r.request_id: r for r in results}
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    correct = n = no_pred = 0
    for item in items:
        if item.category != "priority_tradeoff":
            continue
        n += 1
        gold = item.gold_request.get("tradeoff_profile", "balanced")
        res = by_id.get(item.id)
        pred = res.request.tradeoff_profile if res is not None and res.request is not None else None
        no_pred += pred is None
        correct += pred == gold
        confusion[gold][pred or "none"] += 1
    return {
        "n_items": n,
        "priority_phrase_accuracy": _rate(correct, n),
        "n_no_prediction": no_pred,
        "confusion": {g: dict(c) for g, c in sorted(confusion.items())},
    }


def tradeoff_metrics(items, results, oracle: LabelSettingOracle, tradeoffs_cfg: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "pareto_validity": pareto_validity(items, results, oracle),
        "delta_faithfulness": delta_faithfulness(results),
        "weight_sensitivity": weight_sensitivity(items, results, tradeoffs_cfg),
        "charge_time_sensitivity": charge_time_sensitivity(items, results, tradeoffs_cfg),
        "priority_phrases": priority_phrase_accuracy(items, results),
    }
