"""RQ1 metrics (proposal §V-A). Owner: Ian (Deepti does NOT implement these -- no self-grading).

  * per-field precision / recall / F1 against gold_request; complete-request exact match
  * preference-matching precision / recall (TF-IDF vs SBERT on identical labelled matches)
  * clarification-decision F1
  * silent-assumption rate: share of incomplete safety-critical items where the system
    proceeded without clarifying or rejecting (target for C: <= 2 %)
  * priority-phrase accuracy: predicted tradeoff_profile vs gold (TA-feedback category)

Per-field counting (gold = item.gold_request, prediction = result.request):
  * gold and prediction equal                  -> TP
  * both present but different                 -> FP + FN
  * prediction present, gold absent            -> FP
  * gold present, prediction absent            -> FN
  * both absent                                -> nothing (true negative, not counted)
Amenities are compared as sets of (category, priority, wheelchair_required): each element in
both sets is a TP, each predicted-only element an FP, each gold-only element an FN.

Normalization (`normalize_field`):
  * origin / destination: parsed into (city, state). A trailing US state -- full name or
    two-letter code, e.g. "Flagstaff, Arizona" == "Flagstaff, AZ" -- becomes its code; the rest is
    compared case-, whitespace- and punctuation-insensitively. After a comma the state is matched
    case-insensitively; without a comma a full name ("Bend Oregon") is always recognized, a
    two-letter code only when written in capitals ("Bend OR"), or, in a prediction, in lower case
    when it equals the gold's state code ("phoenix az" vs gold "Phoenix, AZ"). "downtown la" is
    therefore never read as Louisiana unless the gold place is in Louisiana.
    Strict match (primary): city and state both equal, so "Phoenix" != "Phoenix, AZ".
    City-level match (secondary, `level="city"`, reported separately by city_level_place_prf):
    cities equal and the states equal or either one missing; two different states never match.
  * vehicle_id: exact string; numeric fields: exact float equality
  * connectors_override: a set (order-insensitive), compared as one value; [] == absent
  * amenities: set of (category, priority, wheelchair_required) with the contract defaults
    (priority "nice_to_have", wheelchair_required False) filled in; [] == absent
  * tradeoff_profile: "balanced" is the contract default and gold omits it, so "balanced" ==
    absent (only explicitly stated profiles are scored)
weight_overrides is not scored (gold never annotates it).

A result with no request (e.g. a failed run) counts as an empty prediction.
Undefined ratios (0/0) are NaN.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Sequence
from typing import Any

from clickless.contracts.request import SAFETY_CRITICAL_FIELDS

from .stats import bootstrap_ci

PLACE_FIELDS = ("origin", "destination")
NUMERIC_FIELDS = ("start_soc_pct", "arrival_soc_pct_min", "max_detour_min", "min_power_kw")
SCORED_FIELDS: tuple[str, ...] = (
    "origin",
    "destination",
    "vehicle_id",
    "start_soc_pct",
    "arrival_soc_pct_min",
    "max_detour_min",
    "min_power_kw",
    "connectors_override",
    "amenities",
    "tradeoff_profile",
)
_SET_VALUED = ("amenities",)  # set-based P/R (element counts); everything else is one value
_PROCEED = ("ok", "infeasible")  # statuses that mean "planned without asking"
_ASKED = ("needs_clarification", "rejected_incomplete")  # clarify (C) or reject (A/B)

Counts = tuple[int, int, int]  # (tp, fp, fn)
PlaceKey = tuple[str, str | None]  # (normalized city text, two-letter state code or None)

US_STATES: dict[str, str] = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "district of columbia": "DC",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
    "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}  # fmt: skip
_STATE_CODES = frozenset(US_STATES.values())
_WORD = re.compile(r"[^\W_]+")


# --------------------------------------------------------------------------- normalization


def _enum_value(v: Any) -> Any:
    return getattr(v, "value", v)


def _state(words: list[str], allow_lower_code: bool, state_hint: str | None = None) -> str | None:
    """Two-letter code if `words` (original case) spell a US state name or code, else None.

    A lower-case code counts when `allow_lower_code`, or when it equals `state_hint`.
    """
    joined = " ".join(words).casefold()
    if joined in US_STATES:
        return US_STATES[joined]
    if len(words) == 1 and (code := words[0].upper()) in _STATE_CODES:
        if allow_lower_code or words[0].isupper() or code == state_hint:
            return code
    return None


def _place_text(value: Any) -> Any:
    return value.get("text") if isinstance(value, dict) else getattr(value, "text", value)


def parse_place(text: Any, state_hint: str | None = None) -> PlaceKey | None:
    """(city, state) for a place string; None if it has no letters or digits. See module docstring.

    `state_hint` (the gold's state code when parsing a prediction) lets a trailing lower-case
    two-letter token without a comma count as a state, but only when it equals the hint:
    "phoenix az" with hint "AZ" -> ("phoenix", "AZ"); "downtown la" with hint "CA" stays a city.
    """
    if text is None:
        return None
    text = str(text)
    city_words, state = _WORD.findall(text), None
    if "," in text:
        head, tail = text.rsplit(",", 1)
        state = _state(_WORD.findall(tail), allow_lower_code=True)
        if state:
            city_words = _WORD.findall(head)
    if state is None:
        for k in (3, 2, 1):  # longest state name first ("district of columbia", "new york")
            if len(city_words) > k and (state := _state(city_words[-k:], False, state_hint)):
                city_words = city_words[:-k]
                break
    city = "".join(city_words).casefold()
    if not city:  # e.g. ", AZ": nothing but a state
        return (state.casefold(), None) if state else None
    return (city, state)


def city_level_match(gold: PlaceKey, pred: PlaceKey) -> bool:
    """Same city, and the same state or a state missing on either side."""
    return gold[0] == pred[0] and (gold[1] == pred[1] or gold[1] is None or pred[1] is None)


def normalize_field(name: str, value: Any) -> Any:
    """Canonical comparable form of one TripRequest field value; None means 'absent'.

    Accepts gold-style values (plain strings / dicts) and contract objects (Place,
    AmenityPreference, Connector enums) alike.
    """
    if value is None:
        return None
    if name in PLACE_FIELDS:
        return parse_place(_place_text(value))
    if name in NUMERIC_FIELDS:
        return float(value)
    if name == "vehicle_id":
        return str(value) if str(value) != "" else None
    if name == "connectors_override":
        conns = frozenset(str(_enum_value(c)) for c in value)
        return conns or None
    if name == "amenities":
        prefs = set()
        for a in value:
            get = a.get if isinstance(a, dict) else lambda k, d=None, a=a: getattr(a, k, d)
            prefs.add(
                (
                    str(_enum_value(get("category"))),
                    str(get("priority", "nice_to_have") or "nice_to_have"),
                    bool(get("wheelchair_required", False)),
                )
            )
        return frozenset(prefs) or None
    if name == "tradeoff_profile":
        v = str(_enum_value(value))
        return None if v == "balanced" else v
    return value


def _get(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)


def _check_pairs(items: Sequence, results: Sequence) -> list[tuple[Any, Any]]:
    pairs = list(zip(items, results, strict=True))
    for item, res in pairs:
        rid = getattr(res, "request_id", None)
        if rid is not None and rid != item.id:
            raise ValueError(f"item/result mismatch: {item.id} vs {rid}")
    return pairs


# --------------------------------------------------------------------------- per-field P/R/F1


def field_counts(name: str, gold: Any, pred: Any, level: str = "strict") -> Counts:
    """(tp, fp, fn) for one field of one item, from raw (un-normalized) values.

    level="city" applies city_level_match to origin/destination (ignored for other fields).
    """
    g = normalize_field(name, gold)
    if name in PLACE_FIELDS:  # the gold's state lets a lower-case "az" suffix count (see parse_place)
        p = parse_place(_place_text(pred), state_hint=g[1] if g else None) if pred is not None else None
    else:
        p = normalize_field(name, pred)
    if name in _SET_VALUED:
        g, p = g or frozenset(), p or frozenset()
        return len(g & p), len(p - g), len(g - p)
    if g is None and p is None:
        return 0, 0, 0
    if g is None:
        return 0, 1, 0
    if p is None:
        return 0, 0, 1
    same = city_level_match(g, p) if level == "city" and name in PLACE_FIELDS else g == p
    return (1, 0, 0) if same else (0, 1, 1)


def item_field_counts(item, result, level: str = "strict") -> dict[str, Counts]:
    """Per-field (tp, fp, fn) for one benchmark item and its PlanResult."""
    pred = getattr(result, "request", None)
    return {f: field_counts(f, item.gold_request.get(f), _get(pred, f), level) for f in SCORED_FIELDS}


def item_exact_match(item, result) -> bool:
    """True iff every scored field matches (both absent counts as a match)."""
    return all(fp == 0 and fn == 0 for _, fp, fn in item_field_counts(item, result).values())


def prf(tp: int, fp: int, fn: int) -> dict[str, float]:
    """Precision, recall, F1 = 2TP / (2TP + FP + FN); NaN when undefined."""
    nan = float("nan")
    return {
        "precision": tp / (tp + fp) if tp + fp else nan,
        "recall": tp / (tp + fn) if tp + fn else nan,
        "f1": 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else nan,
    }


def _sum_counts(per_item: Sequence[dict[str, Counts]], fields: Sequence[str]) -> Counts:
    tp = sum(c[f][0] for c in per_item for f in fields)
    fp = sum(c[f][1] for c in per_item for f in fields)
    fn = sum(c[f][2] for c in per_item for f in fields)
    return tp, fp, fn


def extraction_prf(items, results) -> dict[str, float]:
    """Micro and per-field P/R/F1 over SCORED_FIELDS plus complete-request exact match.

    Keys: micro_{precision,recall,f1}, exact_match, n, and for each field
    "<field>.{precision,recall,f1,tp,fp,fn}".
    """
    pairs = _check_pairs(items, results)
    per_item = [item_field_counts(i, r) for i, r in pairs]
    out: dict[str, float] = {"n": len(pairs)}
    out.update({f"micro_{k}": v for k, v in prf(*_sum_counts(per_item, SCORED_FIELDS)).items()})
    out["exact_match"] = (
        sum(item_exact_match(i, r) for i, r in pairs) / len(pairs) if pairs else float("nan")
    )
    for f in SCORED_FIELDS:
        tp, fp, fn = _sum_counts(per_item, [f])
        out.update({f"{f}.{k}": v for k, v in prf(tp, fp, fn).items()})
        out.update({f"{f}.tp": tp, f"{f}.fp": fp, f"{f}.fn": fn})
    return out


def city_level_place_prf(items, results) -> dict[str, float]:
    """Secondary place metric: P/R/F1 for origin, destination and both pooled ("places.*"),
    counting a prediction as correct when it matches at city level (a missing state suffix on
    either side is ignored). Reported separately; extraction_prf stays strict.
    """
    pairs = _check_pairs(items, results)
    per_item = [item_field_counts(i, r, level="city") for i, r in pairs]
    out: dict[str, float] = {"n": len(pairs)}
    for name, fields in (("origin", ["origin"]), ("destination", ["destination"]), ("places", PLACE_FIELDS)):
        tp, fp, fn = _sum_counts(per_item, fields)
        out.update({f"{name}.{k}": v for k, v in prf(tp, fp, fn).items()})
        out.update({f"{name}.tp": tp, f"{name}.fp": fp, f"{name}.fn": fn})
    return out


# --------------------------------------------------------------------------- clarification


def asked(result) -> bool:
    """The system asked for clarification (C) or rejected as incomplete (A/B)."""
    return getattr(result, "status", None) in _ASKED


def clarification_prf(items, results) -> dict[str, float]:
    """Clarification-decision P/R/F1; positive class = clarification_required.

    Predicted positive = status needs_clarification or rejected_incomplete.
    """
    pairs = _check_pairs(items, results)
    tp = sum(i.clarification_required and asked(r) for i, r in pairs)
    fp = sum((not i.clarification_required) and asked(r) for i, r in pairs)
    fn = sum(i.clarification_required and not asked(r) for i, r in pairs)
    tn = len(pairs) - tp - fp - fn
    return {**prf(tp, fp, fn), "tp": tp, "fp": fp, "fn": fn, "tn": tn, "n": len(pairs)}


def predicted_missing_fields(result) -> set[str]:
    """Safety-critical fields the parser left missing/unresolved (TripRequest.missing_safety_critical)."""
    req = getattr(result, "request", None)
    if req is None:
        return set(SAFETY_CRITICAL_FIELDS)
    return set(req.missing_safety_critical())


def missing_field_match(items, results) -> dict[str, float]:
    """Exact set match of predicted vs gold missing_safety_fields.

    Reported over all items and over the gold-incomplete subset (clarification_required).
    """
    pairs = _check_pairs(items, results)
    hits = [set(i.missing_safety_fields) == predicted_missing_fields(r) for i, r in pairs]
    inc = [h for h, (i, _) in zip(hits, pairs, strict=True) if i.clarification_required]
    nan = float("nan")
    return {
        "missing_field_exact_match": sum(hits) / len(hits) if hits else nan,
        "missing_field_exact_match_incomplete": sum(inc) / len(inc) if inc else nan,
        "n": len(hits),
        "n_incomplete": len(inc),
    }


def silent_assumption_rate(items, results) -> dict[str, float]:
    """Proceeded (status ok/infeasible) on an item with clarification_required=True."""
    flagged = [(i, r) for i, r in zip(items, results, strict=True) if i.clarification_required]
    if not flagged:
        return {"silent_assumption_rate": float("nan"), "n_incomplete": 0}
    silent = sum(r.status in ("ok", "infeasible") for _, r in flagged)
    return {"silent_assumption_rate": silent / len(flagged), "n_incomplete": len(flagged)}


# --------------------------------------------------------------------------- bootstrap CIs


def understanding_cis(items, results, n: int = 10_000, seed: int = 573) -> dict[str, list[float]]:
    """95 % percentile bootstrap CIs (resampling items) for the headline RQ1 numbers.

    Keys: micro_f1, exact_match, "<field>.f1", "city_level.{origin,destination,places}.f1",
    clarification_f1, silent_assumption_rate.
    Each value is [lo, hi]. Every statistic uses the same seed, so the resampled item sets are
    identical across statistics.
    """
    pairs = _check_pairs(items, results)
    recs = [
        {
            "counts": item_field_counts(i, r),
            "city": item_field_counts(i, r, level="city"),
            "exact": item_exact_match(i, r),
            "clar": (i.clarification_required, asked(r)),
            "silent": (r.status in _PROCEED) if i.clarification_required else None,
        }
        for i, r in pairs
    ]

    def f1_of(fields: Sequence[str], key: str = "counts") -> Callable[[list[dict]], float]:
        return lambda s: prf(*_sum_counts([x[key] for x in s], fields))["f1"]

    def clar_f1(s: list[dict]) -> float:
        tp = sum(g and p for g, p in (x["clar"] for x in s))
        fp = sum((not g) and p for g, p in (x["clar"] for x in s))
        fn = sum(g and not p for g, p in (x["clar"] for x in s))
        return prf(tp, fp, fn)["f1"]

    def silent(s: list[dict]) -> float:
        v = [x["silent"] for x in s if x["silent"] is not None]
        return sum(v) / len(v) if v else math.nan

    stats: dict[str, Callable[[list[dict]], float]] = {
        "micro_f1": f1_of(SCORED_FIELDS),
        "exact_match": lambda s: sum(x["exact"] for x in s) / len(s),
        **{f"{f}.f1": f1_of([f]) for f in SCORED_FIELDS},
        "city_level.origin.f1": f1_of(["origin"], "city"),
        "city_level.destination.f1": f1_of(["destination"], "city"),
        "city_level.places.f1": f1_of(PLACE_FIELDS, "city"),
        "clarification_f1": clar_f1,
        "silent_assumption_rate": silent,
    }
    return {k: list(bootstrap_ci(recs, n=n, seed=seed, statistic=fn)) for k, fn in stats.items()}
