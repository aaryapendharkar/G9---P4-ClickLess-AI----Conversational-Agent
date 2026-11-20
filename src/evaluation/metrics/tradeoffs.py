"""Trade-off metrics (TA feedback). Owner: Ian. See docs/TRADEOFFS.md section 5.

  * pareto_validity: shown alternatives must be non-dominated among the ORACLE's feasible plans on
    (total time, detour, charge time), within the oracle's pareto_tol_min. Each shown alternative is
    counted in exactly one of valid / buffer_only / dominated / amenity_tradeoff / infeasible /
    oracle_infeasible_passes_check / within_tolerance / unpriceable:
      - buffer_only: strictly dominated by the strict oracle front but NOT by the policy front, the
        oracle's search with the planner's charge_buffer_soc_pct (configs/energy.yaml, read through
        the oracle's energy config, never planner code) applied after every charging stop. The
        extra charging minutes versus the strict front's plan over the same stations are reported.
      - amenity_tradeoff: dominated on those three but possibly better on amenities
        (docs/EVALUATION_ORACLE.md gap 12), judged with the PARSED request's preference keys (the
        keys satisfied_preferences was computed with).
      - oracle_infeasible_passes_check: the oracle says the request is infeasible, yet the plan
        passes check_itinerary within tolerance. Never valid.
      - within_tolerance: passes check_itinerary only within soc_tol_pct / detour_tol_min, so the
        strict front cannot be compared with it. In neither rate.
    strict_pareto_validity_rate counts buffer_only as not valid; policy_adjusted_pareto_validity_rate
    removes only the buffer_only cases from the denominator. Amenity trade-offs, within-tolerance and
    unpriceable plans are in neither denominator.
  * delta_faithfulness: every number in an alternative's sentence must equal the matching
    Alternative.deltas value (up to the presenter's whole-minute rounding); mismatches per claim.
    Sentences are split into clauses ("and", "but", "with", ";", ","), one claim per clause;
    minutes and hours are read. A clause with a time unit that cannot be read as exactly one claim is
    counted as "unparsed", which is not faithful.
  * weight_sensitivity: Kendall tau-b of plan rankings across the three profiles and under +-20 %
    weights, plus the flip rate of the plan actually shown first (alternatives[0]).
  * charge_time_sensitivity: the same with charge minutes x0.75 / x1.25 (LEAF excluded, ADR 0009).
  * unknown_power_rank_sensitivity (TRADEOFFS section 5): the same with the oracle's DC-fast fallback
    for unknown-power ports at 25 and 100 kW instead of 50 (only items whose shown plans use one).
    All three sensitivities cover the DISPLAYED plans only: they re-rank the alternatives kept in
    PlanResult, not every verified plan the system considered before selection, so a flip means
    "the order of what the driver saw would change". Items where this module's cost formula would
    not put alternatives[0] first are counted as formula_ranker_mismatch, never as flips.
  * priority_phrase_accuracy: predicted vs gold tradeoff_profile on priority_tradeoff items.

Rankings use the cost formula of docs/TRADEOFFS.md section 2 with configs/tradeoffs.yaml, written
here from the documentation -- never ranking code. How an amenity key missing from
amenity_value_min is valued comes from configs/evaluation.yaml (tradeoffs.amenity_value_fallback,
copied from the ranker; a test checks the two agree). Like the oracle, this module must not import
clickless.planning, ranking, retrieval or pipeline (tests/evaluation/test_oracle_independence.py).
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from functools import lru_cache
from itertools import combinations
from typing import Any

import yaml

from clickless.config import repo_path
from clickless.contracts import Itinerary, PlanResult, TripRequest

from ..oracle.energy_reference import is_charge_time_excluded
from ..oracle.label_setting import LabelSettingOracle, OraclePlan, OracleUnavailable, strictly_dominating
from .planning import ItemEval, _rate, request_from_item

PROFILES = ("balanced", "fastest", "comfort")
WEIGHT_FACTORS = (0.8, 1.2)
CHARGE_FACTORS = (0.75, 1.25)
UNKNOWN_POWER_FALLBACKS_KW = (25.0, 100.0)
SCALAR_WEIGHTS = ("drive_min_weight", "charge_min_weight", "detour_extra_weight", "stop_overhead_min")
# Sensitivity metrics re-rank PlanResult.alternatives only (what the driver saw), never the full set
# of verified plans considered before selection.
DISPLAYED_SCOPE = "displayed_plans_only"
SHOWN_FIRST = 0  # alternatives[0] is the plan the driver saw first (Recommended)


def _mean(values: Sequence[float]) -> float | None:
    vals = [v for v in values if not math.isnan(v)]
    return sum(vals) / len(vals) if vals else None


# --------------------------------------------------------------------------- amenity values (fallback rule)


@lru_cache(maxsize=1)
def _evaluation_yaml() -> Mapping[str, Any]:
    return yaml.safe_load(repo_path("configs/evaluation.yaml").read_text(encoding="utf-8")) or {}


def amenity_rule(evaluation_cfg: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """{"fallback": "base_category" | "none", "default": minutes} from configs/evaluation.yaml."""
    cfg = (evaluation_cfg if evaluation_cfg is not None else _evaluation_yaml()).get("tradeoffs", {})
    fallback = cfg.get("amenity_value_fallback", "base_category")
    if fallback not in ("base_category", "none"):
        raise ValueError(f"tradeoffs.amenity_value_fallback must be base_category or none, got {fallback!r}")
    return {"fallback": fallback, "default": float(cfg.get("amenity_value_default_min", 0.0))}


def amenity_weight_key(weights: Mapping[str, float], key: str, rule: Mapping[str, Any]) -> str | None:
    """The weights key that values preference `key` ("food_wheelchair" -> "amenity_value_min.food" when
    only the base category has a value), or None when the default applies."""
    exact = f"amenity_value_min.{key}"
    if exact in weights:
        return exact
    base = f"amenity_value_min.{key.split('_')[0]}"
    if rule["fallback"] == "base_category" and base in weights:
        return base
    return None


def amenity_value(weights: Mapping[str, float], key: str, rule: Mapping[str, Any]) -> float:
    wkey = amenity_weight_key(weights, key, rule)
    return weights[wkey] if wkey is not None else rule["default"]


# --------------------------------------------------------------------------- Pareto validity


def _amenity_keys(request: TripRequest | None) -> set[str]:
    return {p.key for p in request.amenities} if request is not None else set()


def _amenity_tradeoff(keys: set[str], itin: Itinerary, dominating: list[OraclePlan]) -> bool:
    """Could the dominated alternative still be better on amenities than every plan dominating it?

    Amenity satisfaction is the best match per requested preference over a plan's stops, so a plan
    that visits every station where the alternative satisfies a preference is at least as good on
    amenities. Only when no dominating plan does that can the alternative be an amenity trade-off.
    `keys` are the preference keys satisfied_preferences was computed with (the parsed request's).
    """
    satisfying = {s.station_id for s in itin.stops if any(s.satisfied_preferences.get(k, 0.0) > 0 for k in keys)}
    if not satisfying:
        return False
    return not any(satisfying <= set(p.station_ids) for p in dominating)


def _buffer_baseline(itin: Itinerary, front: list[OraclePlan], dominating: list[OraclePlan]) -> OraclePlan:
    """The strict plan a buffer_only alternative is compared with for its extra charging time: the
    strict front's plan over the same stations with the least charging, else the dominating plan with
    the least charging."""
    stations = tuple(s.station_id for s in itin.stops)
    same = [p for p in front if p.station_ids == stations]
    return min(same or dominating, key=lambda p: (p.objectives[2], p.objectives))


def pareto_validity(
    items,
    results: Sequence[PlanResult],
    oracle: LabelSettingOracle,
    charge_buffer_soc_pct: float | None = None,
    evals: Sequence[ItemEval] | None = None,
) -> dict[str, Any]:
    """Strict Pareto validity of shown alternatives, with buffer-only cases classified separately.

    charge_buffer_soc_pct defaults to the oracle's energy config (configs/energy.yaml). `evals` (from
    planning.evaluate) are reused when given, so feasibility and checks are not computed twice.
    """
    by_id = {r.request_id: r for r in results}
    ev_by_id = {e.item_id: e for e in evals or []}
    tol = oracle.cfg.pareto_tol_min
    buffer_pct = float(
        oracle.cfg.energy["charge_buffer_soc_pct"] if charge_buffer_soc_pct is None else charge_buffer_soc_pct
    )
    policy = oracle.with_overrides(departure_buffer_pct=buffer_pct) if buffer_pct > 0 else oracle
    counts: Counter[str] = Counter()
    details: list[dict[str, Any]] = []
    buffer_extra: list[float] = []
    excluded_incomplete: list[str] = []
    parse_changed: list[str] = []
    for item in items:
        req, res = request_from_item(item), by_id.get(item.id)
        if req is None or res is None or res.status != "ok" or not res.alternatives:
            continue
        ev = ev_by_id.get(item.id)
        fr = ev.feasibility if ev is not None else oracle.is_feasible(req)
        if not fr.evaluable:
            continue
        front: list[OraclePlan] = []
        policy_front: list[OraclePlan] = []
        if fr.feasible:
            search, policy_search = oracle.pareto_plans(req), policy.pareto_plans(req)
            if not (search.complete and policy_search.complete):  # never judged against a partial front
                excluded_incomplete.append(item.id)
                continue
            front, policy_front = search.plans, policy_search.plans
        gold_keys = _amenity_keys(req)
        keys = _amenity_keys(res.request) if res.request is not None else gold_keys
        note = None
        if keys != gold_keys:
            parse_changed.append(item.id)
            note = f"parse_changed_amenities: gold {sorted(gold_keys)}, parsed {sorted(keys)}"
        for k, alt in enumerate(res.alternatives):
            dominating: list[OraclePlan] = []
            baseline: OraclePlan | None = None
            detail: dict[str, Any] = {"item_id": item.id, "label": alt.label}
            rep = ev.reports[k] if ev is not None and k < len(ev.reports) else None
            if rep is None:
                try:
                    rep = oracle.check_itinerary(req, alt.itinerary)
                except OracleUnavailable as e:
                    counts["unpriceable"] += 1
                    details.append({**detail, "category": "unpriceable", "error": str(e)})
                    continue
            if not rep.ok:
                category = "infeasible"
            elif not fr.feasible:
                category = "oracle_infeasible_passes_check"
            elif not rep.strict_ok:
                category = "within_tolerance"
            elif rep.objectives is None:
                category = "unpriceable"
            else:
                dominating = strictly_dominating(rep.objectives, front, tol)
                if not dominating:
                    category = "valid"
                elif policy_front and not strictly_dominating(rep.objectives, policy_front, tol):
                    category = "buffer_only"
                    baseline = _buffer_baseline(alt.itinerary, front, dominating)
                elif _amenity_tradeoff(keys, alt.itinerary, dominating):
                    category = "amenity_tradeoff"
                else:
                    category = "dominated"
            counts[category] += 1
            if category == "valid":
                continue
            detail.update(
                category=category,
                objectives=None if rep.objectives is None else [round(x, 2) for x in rep.objectives],
                dominated_by=[list(p.station_ids) for p in dominating],
                violations=[v.code for v in rep.violations],
            )
            if category == "within_tolerance":
                detail["strict_violations"] = rep.strict_violations
            if note:
                detail["note"] = note
            if baseline is not None:
                extra = rep.objectives[2] - baseline.objectives[2]
                buffer_extra.append(extra)
                detail["buffer_baseline_objectives"] = [round(x, 2) for x in baseline.objectives]
                detail["extra_charge_min"] = round(extra, 2)
            details.append(detail)
    strict_den = sum(counts[c] for c in ("valid", "buffer_only", "dominated", "infeasible"))
    strict_den += counts["oracle_infeasible_passes_check"]
    return {
        "n_shown": sum(counts.values()),
        "n_valid": counts["valid"],
        "n_buffer_only": counts["buffer_only"],  # dominated only because of charge_buffer_soc_pct
        "n_dominated": counts["dominated"],
        "n_amenity_tradeoff": counts["amenity_tradeoff"],  # reported separately, in neither rate
        "n_infeasible": counts["infeasible"],
        "n_oracle_infeasible_passes_check": counts["oracle_infeasible_passes_check"],  # never valid
        "n_within_tolerance": counts["within_tolerance"],  # in neither rate
        "n_unpriceable": counts["unpriceable"],
        "strict_pareto_validity_rate": _rate(counts["valid"], strict_den),
        "policy_adjusted_pareto_validity_rate": _rate(counts["valid"], strict_den - counts["buffer_only"]),
        "buffer_only_extra_charge_min_total": sum(buffer_extra),
        "buffer_only_extra_charge_min_mean": _mean(buffer_extra),
        "charge_buffer_soc_pct": buffer_pct,
        "tolerance_min": tol,
        "not_valid": details,
        "n_items_excluded_incomplete_search": len(excluded_incomplete),
        "items_excluded_incomplete_search": excluded_incomplete,
        "items_parse_changed_amenities": parse_changed,
    }


# --------------------------------------------------------------------------- delta faithfulness

_UNIT = r"(?P<unit>minutes?|mins?|hours?|hrs?|h)\b"
_CLAIM = re.compile(rf"(?P<sign>[+\-−])?\s*(?P<num>\d+(?:\.\d+)?)\s*{_UNIT}", re.IGNORECASE)
_HAS_UNIT = re.compile(r"\b(?:minutes?|mins?|hours?|hrs?)\b", re.IGNORECASE)
# Clause boundaries: separators and the conjunctions the presenter uses to join two claims. A period
# ends a clause only when it is not a decimal point.
_CLAUSES = re.compile(r";|,|\(|\)|\.(?!\d)|\b(?:and|but|with)\b", re.IGNORECASE)
# The delta a clause talks about: detour and charging are named; anything else about time is total.
_TOTAL_WORDS = re.compile(r"\b(?:quicker|faster|shorter|sooner|slower|longer|later|total|overall|saved|saves)\b")
_LESS = re.compile(r"\b(?:less|fewer|quicker|faster|shorter|sooner|saved|saves|saving)\b")
_MORE = re.compile(r"\b(?:more|extra|longer|slower|later|added|additional)\b")


def _key_of(words: str) -> str | None:
    if "detour" in words:
        return "detour_min"
    if "charg" in words:
        return "charge_min"
    if _TOTAL_WORDS.search(words):
        return "total_time_min"
    return None


def _direction(words: str) -> int:
    """-1 for "less/quicker/...", +1 for "more/longer/..." or nothing; the first such word wins."""
    less, more = _LESS.search(words), _MORE.search(words)
    if less and (not more or less.start() < more.start()):
        return -1
    return 1


def parse_claims(text: str) -> list[dict[str, Any]]:
    """One claim per clause: {"text", "key" (delta key or None), "claimed" (signed minutes)}.

    A clause that mentions a time unit but does not hold exactly one "<number> <unit>" is returned as
    {"text", "key": None, "claimed": None, "unparsed": True}, never guessed."""
    claims: list[dict[str, Any]] = []
    for clause in _CLAUSES.split(text):
        clause = clause.strip()
        if not clause:
            continue
        found = list(_CLAIM.finditer(clause))
        if len(found) != 1:
            if found or _HAS_UNIT.search(clause):
                claims.append({"text": clause, "key": None, "claimed": None, "unparsed": True})
            continue
        m = found[0]
        words = f"{clause[: m.start()]} {clause[m.end():]}".lower()
        minutes = float(m.group("num")) * (60.0 if m.group("unit").lower().startswith("h") else 1.0)
        sign = -1 if m.group("sign") in ("-", "−") else 1
        claims.append(
            {"text": clause[m.start() :].strip(), "key": _key_of(words), "claimed": sign * _direction(words) * minutes}
        )
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
            # The label itself ("Fastest: ...") is not a claim.
            for c in parse_claims(text[len(alt.label) + 1 :]):
                actual = alt.deltas.get(c["key"]) if c["key"] else None
                if c.get("unparsed"):
                    reason = "unparsed"
                elif c["key"] is None:
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
        "n_unparsed": sum(1 for c in claims if c.get("unparsed")),
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
    rule: Mapping[str, Any] | None = None,
) -> float:
    """cost = w_drive*drive + w_charge*charge + detour_extra*detour + overhead*stops
    - sum over REQUESTED preferences of amenity value * best match score at any stop - evidence.
    A preference with no value of its own uses the fallback rule (amenity_rule())."""
    rule = rule or amenity_rule()
    cost = (
        weights["drive_min_weight"] * itin.total_drive_min
        + weights["charge_min_weight"] * itin.total_charge_min * charge_scale
        + weights["detour_extra_weight"] * itin.total_detour_min
        + weights["stop_overhead_min"] * len(itin.stops)
    )
    for key in {p.key for p in request.amenities}:  # once per trip, at its best stop
        best = max((s.satisfied_preferences.get(key, 0.0) for s in itin.stops), default=0.0)
        cost -= amenity_value(weights, key, rule) * best
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


def _costs(req, itins, evidence, weights, charge_scale=1.0, rule=None) -> list[float]:
    return [plan_cost(it, req, weights, ev, charge_scale, rule) for it, ev in zip(itins, evidence, strict=True)]


def perturbation_keys(req: TripRequest, weights: Mapping[str, float], rule) -> tuple[list[str], int]:
    """(weights worth perturbing, number skipped). A weight is perturbed once even if several
    preferences resolve to it, and not at all when it is 0: x0.8 / x1.2 of 0 cannot change a ranking,
    so counting it as "stable" would bias the flip rate and Kendall tau."""
    candidates = list(SCALAR_WEIGHTS)
    for key in sorted({p.key for p in req.amenities}):
        candidates.append(amenity_weight_key(weights, key, rule) or f"amenity_value_min.{key}")
    keys, skipped = [], 0
    for k in dict.fromkeys(candidates):  # dedupe, keep order
        if weights.get(k, 0.0) == 0.0:
            skipped += 1
        else:
            keys.append(k)
    return keys, skipped


def weight_sensitivity(
    items, results: Sequence[PlanResult], tradeoffs_cfg: Mapping[str, Any], rule=None
) -> dict[str, Any]:
    """Displayed-plan weight sensitivity: re-ranks only the alternatives shown in each PlanResult.

    A flip means a perturbed ranking would no longer put alternatives[0] (the plan shown first) on
    top. An item whose unperturbed cost under this formula already ranks another alternative first
    (e.g. a trained ranker disagrees with the formula) is a formula_ranker_mismatch and is left out
    of the perturbation metrics instead of being counted as flipping every time.
    """
    rule = rule or amenity_rule()
    profile_tau: dict[str, list[float]] = defaultdict(list)
    profile_flip: dict[str, list[int]] = defaultdict(list)
    pert_tau, pert_flip, item_flip, mismatch = [], [], [], []
    n = skipped_zero = 0
    for item_id, req, itins, ev in _cases(items, results):
        n += 1
        by_profile = {
            p: _costs(req, itins, ev, profile_weights(tradeoffs_cfg, p, req.weight_overrides), rule=rule)
            for p in PROFILES
        }
        for a, b in combinations(PROFILES, 2):
            profile_tau[f"{a}_vs_{b}"].append(kendall_tau_b(by_profile[a], by_profile[b]))
            profile_flip[f"{a}_vs_{b}"].append(int(_top(by_profile[a]) != _top(by_profile[b])))
        base_w = profile_weights(tradeoffs_cfg, req.tradeoff_profile, req.weight_overrides)
        base = _costs(req, itins, ev, base_w, rule=rule)
        if _top(base) != SHOWN_FIRST:
            mismatch.append(item_id)
            continue
        keys, skipped = perturbation_keys(req, base_w, rule)
        skipped_zero += skipped
        flipped = False
        for key in keys:
            for f in WEIGHT_FACTORS:
                costs = _costs(req, itins, ev, {**base_w, key: base_w[key] * f}, rule=rule)
                pert_tau.append(kendall_tau_b(base, costs))
                flip = _top(costs) != SHOWN_FIRST
                pert_flip.append(int(flip))
                flipped |= flip
        item_flip.append(int(flipped))
    out: dict[str, Any] = {"scope": DISPLAYED_SCOPE, "n_items": n}
    for pair in profile_tau:
        out[f"kendall_tau_{pair}"] = _mean(profile_tau[pair])
        out[f"flip_rate_{pair}"] = _rate(sum(profile_flip[pair]), len(profile_flip[pair]))
    out["kendall_tau_pm20"] = _mean(pert_tau)
    out["flip_rate_pm20"] = _rate(sum(pert_flip), len(pert_flip))  # over (item, perturbation)
    out["item_flip_rate_pm20"] = _rate(sum(item_flip), len(item_flip))  # any perturbation flips the item
    out["n_perturbations"] = len(pert_flip)
    out["n_perturbations_skipped_zero"] = skipped_zero
    out["n_formula_ranker_mismatch"] = len(mismatch)
    out["items_formula_ranker_mismatch"] = mismatch
    return out


def charge_time_sensitivity(
    items,
    results: Sequence[PlanResult],
    tradeoffs_cfg: Mapping[str, Any],
    factors: Sequence[float] = CHARGE_FACTORS,
    rule=None,
) -> dict[str, Any]:
    """Displayed-plan charge-time sensitivity: re-ranks only the alternatives shown in each PlanResult;
    a flip means alternatives[0] would no longer be on top."""
    rule = rule or amenity_rule()
    taus: dict[float, list[float]] = defaultdict(list)
    flips: dict[float, list[int]] = defaultdict(list)
    n = leaf = 0
    mismatch: list[str] = []
    for item_id, req, itins, ev in _cases(items, results):
        if is_charge_time_excluded(req.vehicle_id):
            leaf += 1
            continue
        n += 1
        w = profile_weights(tradeoffs_cfg, req.tradeoff_profile, req.weight_overrides)
        base = _costs(req, itins, ev, w, rule=rule)
        if _top(base) != SHOWN_FIRST:
            mismatch.append(item_id)
            continue
        for f in factors:
            costs = _costs(req, itins, ev, w, charge_scale=f, rule=rule)
            taus[f].append(kendall_tau_b(base, costs))
            flips[f].append(int(_top(costs) != SHOWN_FIRST))
    out: dict[str, Any] = {"scope": DISPLAYED_SCOPE, "n_items": n, "n_leaf_excluded_from_charge_time": leaf}
    for f in factors:
        out[f"kendall_tau_x{f:g}"] = _mean(taus[f])
        out[f"flip_rate_x{f:g}"] = _rate(sum(flips[f]), len(flips[f]))
    out["n_formula_ranker_mismatch"] = len(mismatch)
    out["items_formula_ranker_mismatch"] = mismatch
    return out


def unknown_power_rank_sensitivity(
    items,
    results: Sequence[PlanResult],
    oracle: LabelSettingOracle,
    tradeoffs_cfg: Mapping[str, Any],
    fallbacks_kw: Sequence[float] = UNKNOWN_POWER_FALLBACKS_KW,
    rule=None,
) -> dict[str, Any]:
    """TRADEOFFS section 5: re-rank the shown alternatives with the oracle's DC-fast fallback for
    unknown-power ports at each of `fallbacks_kw` instead of the baseline. Each alternative's charge
    minutes move by the oracle's own difference (replayed on the gold request); everything else in
    its cost stays. Only items where some shown alternative charges at an unknown-power port; the
    LEAF is excluded (ADR 0009)."""
    rule = rule or amenity_rule()
    by_id = {r.request_id: r for r in results}
    variants = {
        kw: oracle.with_overrides(unknown_power_kw={**(oracle.cfg.unknown_power_kw or {}), "dc_fast": float(kw)})
        for kw in fallbacks_kw
    }
    taus: dict[float, list[float]] = defaultdict(list)
    flips: dict[float, list[int]] = defaultdict(list)
    n = leaf = 0
    mismatch: list[str] = []
    for item in items:
        res, gold = by_id.get(item.id), request_from_item(item)
        if res is None or res.request is None or gold is None or len(res.alternatives) < 2:
            continue
        if is_charge_time_excluded(gold.vehicle_id):
            leaf += 1
            continue
        itins = [a.itinerary for a in res.alternatives]
        try:
            reports = [oracle.check_itinerary(gold, it) for it in itins]
        except OracleUnavailable:
            continue
        if not any(r.uses_unknown_power for r in reports) or any(r.objectives is None for r in reports):
            continue
        n += 1
        w = profile_weights(tradeoffs_cfg, res.request.tradeoff_profile, res.request.weight_overrides)
        base = _costs(res.request, itins, [a.cost.evidence_credit_min for a in res.alternatives], w, rule=rule)
        if _top(base) != SHOWN_FIRST:
            mismatch.append(item.id)
            continue
        for kw, variant in variants.items():
            moved = [
                variant.check_itinerary(gold, it).objectives[2] - r.objectives[2]
                for it, r in zip(itins, reports, strict=True)
            ]
            costs = [b + w["charge_min_weight"] * d for b, d in zip(base, moved, strict=True)]
            taus[kw].append(kendall_tau_b(base, costs))
            flips[kw].append(int(_top(costs) != SHOWN_FIRST))
    out: dict[str, Any] = {
        "scope": DISPLAYED_SCOPE,
        "n_items": n,
        "n_leaf_excluded_from_charge_time": leaf,
        "baseline_dc_fast_kw": float(
            (oracle.cfg.unknown_power_kw or {}).get("dc_fast", oracle.cfg.charging_model["unknown_power_kw"]["dc_fast"])
        ),
    }
    for kw in fallbacks_kw:
        out[f"kendall_tau_{kw:g}kw"] = _mean(taus[kw])
        out[f"flip_rate_{kw:g}kw"] = _rate(sum(flips[kw]), len(flips[kw]))
    out["n_formula_ranker_mismatch"] = len(mismatch)
    out["items_formula_ranker_mismatch"] = mismatch
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
        # Unstated means balanced; an explicit null (e.g. an incomplete priority item) is its own row.
        gold = item.gold_request.get("tradeoff_profile", "balanced")
        res = by_id.get(item.id)
        pred = res.request.tradeoff_profile if res is not None and res.request is not None else None
        no_pred += pred is None
        correct += pred is not None and pred == gold
        confusion["none" if gold is None else str(gold)][pred or "none"] += 1
    return {
        "n_items": n,
        "priority_phrase_accuracy": _rate(correct, n),
        "n_no_prediction": no_pred,
        "confusion": {g: dict(c) for g, c in sorted(confusion.items(), key=lambda kv: str(kv[0]))},
    }


def tradeoff_metrics(
    items,
    results,
    oracle: LabelSettingOracle,
    tradeoffs_cfg: Mapping[str, Any],
    evaluation_cfg: Mapping[str, Any] | None = None,
    evals: Sequence[ItemEval] | None = None,
) -> dict[str, Any]:
    rule = amenity_rule(evaluation_cfg)
    return {
        "pareto_validity": pareto_validity(items, results, oracle, evals=evals),
        "delta_faithfulness": delta_faithfulness(results),
        "displayed_plan_weight_sensitivity": weight_sensitivity(items, results, tradeoffs_cfg, rule),
        "displayed_plan_charge_time_sensitivity": charge_time_sensitivity(items, results, tradeoffs_cfg, rule=rule),
        "displayed_plan_unknown_power_sensitivity": unknown_power_rank_sensitivity(
            items, results, oracle, tradeoffs_cfg, rule=rule
        ),
        "priority_phrases": priority_phrase_accuracy(items, results),
    }
