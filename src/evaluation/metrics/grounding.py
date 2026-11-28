"""Grounding metrics (proposal §V-C). Owner: Ian.

  * atomic-claim evidence support, FActScore-style (target >= 95 %) over PlanResult.claims
  * data-status (freshness) mislabeling rate (target 0 %)
  * KG evidence coverage of the recommended plan's displayed stop facts, and the share of claims
    that cite the KG (`kg_evidence`): what separates C from the C-noKG ablation, since claim
    support is 1.0 for both by construction (template explanations, ADR 0017)
  * provenance completeness of the displayed stop facts (#186): the supporting fact's provenance
    has source, source_record_id and retrieved_at (clickless.data.validate requires source and
    retrieved_at of every record); source_updated_at, optional there, is reported as a share
  * ontology adequacy: in evaluation.metrics.ontology_adequacy (#158), which counts the KG's
    competency-question inventory

Claim support rules (agreed on PR #66, ADR 0015 section 5)
------------------------------------------------------------
A claim is supported only when its `supported_by` list is non-empty AND every id it cites supports
it. One unresolvable or unsupporting citation makes the whole claim unsupported (a fabricated
citation is never excused by a valid one next to it).

1. Per-result lookup. A `kg:` id supports a claim only if it appears in THAT result's own
   `PlanResult.evidence`. There is no global evidence table, so a fact the driver was not shown
   (or a fact from another result) never counts.
2. `verified:` ids resolve against the PlanResult itself, in the form
   `verified:<request_id>:<part>`:
     - `<request_id>` must equal `result.request_id`;
     - the result's `status` must be "ok" (verified ids are not valid for any other status);
     - `stop:<i>` needs `alternatives[0].itinerary.stops[i]` (the recommended itinerary);
     - `alternative:<i>` needs `alternatives[i]` with i >= 1 (index 0 is the recommendation);
     - `arrival` and `cost` need a non-empty `alternatives` (they describe `alternatives[0]`).
   Any other form (malformed, negative or out-of-range index, unknown part) is unsupported.
3. An empty `supported_by` is unsupported. So is any id with another prefix.
4. Unknown values never support specifics:
     - a `ports[i].power_kw` fact with value None cannot support a claim that states a power
       number ("150 kW"); likewise `verified:...:stop:<i>` when that stop's `port_power_kw` is None;
     - a cited `wheelchair` fact (values per ADR 0011: yes / limited / no / unknown) must match the
       accessibility the claim states: "accessible" only by `yes`, "not accessible" /
       "inaccessible" only by `no`, "limited" / "partial" accessibility only by `limited`.
       `unknown` (or None) supports none of them.
   Citing them for what they do say ("power not reported", "accessibility unknown") is fine.
5. Configs A/B (and the C-without-KG ablation) have `evidence == []`, so they are scored on their
   `verified:` claims only: any `kg:` citation in such a result is unsupported by rule 1.

Freshness mislabel rate
-----------------------
For each `  Source: <label>` line the explainer prints under a recommended stop, the expected label
is `clickless.data.provenance.status_label(provenance)` of that stop's station record. The
provenance is read from the result's own station facts in `evidence` (the KG copies the record's
provenance; distance facts are DERIVED and skipped), or from `store` when given and the result has
no station fact. A label that cannot be attributed to a stop, or whose station provenance is not
available, is counted as unverifiable and left out of the rate.

Like the oracle, this module must not import clickless.planning, ranking, retrieval or pipeline.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from typing import Any

from clickless.contracts import Evidence, PlanResult, Provenance
from clickless.data.provenance import status_label

KG_PREFIX = "kg:"
VERIFIED_PREFIX = "verified:"

_POWER_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\s*kw\b", re.IGNORECASE)
_ACCESS_WORD = re.compile(r"\b(?:access|accessib\w*)\b", re.IGNORECASE)
_LIMITED = re.compile(r"\b(?:limited|partial|partially)\b", re.IGNORECASE)
_NOT_ACCESSIBLE = re.compile(
    r"\binaccessible\b|\b(?:not|isn't|is not|aren't|are not)\s+(?:wheelchair[- ])?accessible\b", re.IGNORECASE
)
_ACCESSIBLE = re.compile(r"\baccessible\b", re.IGNORECASE)
_POWER_FIELD = re.compile(r"^ports\[\d+\]\.power_kw$")
_INDEX = re.compile(r"^(0|[1-9]\d*)$")


def _rate(num: int, den: int) -> float | None:
    return num / den if den else None


def states_power_number(text: str) -> bool:
    """The claim states a specific charging power, e.g. '150 kW'."""
    return bool(_POWER_NUMBER.search(text))


def stated_accessibility(text: str) -> str | None:
    """The wheelchair value a claim asserts: 'limited', 'no', 'yes', or None if it asserts none.

    Checked in that order, so 'partially accessible' is limited and 'not accessible' is no.
    'Accessibility unknown' asserts nothing and returns None.
    """
    if _ACCESS_WORD.search(text) and _LIMITED.search(text):
        return "limited"
    if _NOT_ACCESSIBLE.search(text):
        return "no"
    if _ACCESSIBLE.search(text):
        return "yes"
    return None


# --------------------------------------------------------------------------- per-citation checks


def _check_kg(evidence_id: str, text: str, lookup: dict[str, Evidence]) -> str | None:
    """None when the kg: id supports the claim, else the reason it does not."""
    fact = lookup.get(evidence_id)
    if fact is None:
        return "kg id not in this result's evidence"
    if _POWER_FIELD.match(fact.field) and fact.value is None and states_power_number(text):
        return "unknown power cited for a stated power"
    if fact.field == "wheelchair":
        stated = stated_accessibility(text)
        if stated is not None and fact.value != stated:
            return f"wheelchair={fact.value} cannot support a '{stated}' accessibility claim"
    return None


def _check_verified(evidence_id: str, text: str, result: PlanResult) -> str | None:
    """None when the verified: id resolves against this PlanResult, else the reason it does not."""
    prefix = f"{VERIFIED_PREFIX}{result.request_id}:"
    if not evidence_id.startswith(prefix):
        return "verified id names another request"
    if result.status != "ok":
        return "verified id on a result that is not ok"
    part = evidence_id[len(prefix) :].split(":")
    alternatives = result.alternatives
    if part in (["arrival"], ["cost"]):
        return None if alternatives else "no recommendation"
    if len(part) != 2 or not _INDEX.match(part[1]):
        return "malformed verified id"
    kind, i = part[0], int(part[1])
    if kind == "stop":
        if not alternatives:
            return "no recommendation"
        stops = alternatives[0].itinerary.stops
        if i >= len(stops):
            return "no such stop in the recommendation"
        if stops[i].port_power_kw is None and states_power_number(text):
            return "unknown power cited for a stated power"
        return None
    if kind == "alternative":
        if i < 1:
            return "alternative index must be >= 1"
        return None if i < len(alternatives) else "no such alternative"
    return "malformed verified id"


def check_claim(claim, result: PlanResult, lookup: dict[str, Evidence] | None = None) -> str | None:
    """None when the claim is supported under the module rules, else the first reason it is not."""
    if not claim.supported_by:
        return "empty supported_by"
    if lookup is None:
        lookup = {e.evidence_id: e for e in result.evidence}
    for evidence_id in claim.supported_by:
        if evidence_id.startswith(KG_PREFIX):
            reason = _check_kg(evidence_id, claim.text, lookup)
        elif evidence_id.startswith(VERIFIED_PREFIX):
            reason = _check_verified(evidence_id, claim.text, result)
        else:
            reason = "unknown evidence id prefix"
        if reason is not None:
            return f"{evidence_id}: {reason}"
    return None


def _kind(claim) -> str:
    """'kg', 'verified', 'mixed', 'other' or 'none' by the citations' prefixes."""
    kinds = {
        "kg" if e.startswith(KG_PREFIX) else "verified" if e.startswith(VERIFIED_PREFIX) else "other"
        for e in claim.supported_by
    }
    if not kinds:
        return "none"
    return kinds.pop() if len(kinds) == 1 else "mixed"


# --------------------------------------------------------------------------- claim support


def claim_support(results: Sequence[PlanResult]) -> dict[str, Any]:
    """Share of PlanResult.claims that are supported, using each result's OWN evidence only."""
    n = supported = 0
    by_kind: dict[str, Counter[str]] = {}
    unsupported: list[dict[str, Any]] = []
    for result in results:
        lookup = {e.evidence_id: e for e in result.evidence}  # per result, never shared
        for claim in result.claims:
            n += 1
            reason = check_claim(claim, result, lookup)
            kind = _kind(claim)
            by_kind.setdefault(kind, Counter())["n"] += 1
            if reason is None:
                supported += 1
                by_kind[kind]["supported"] += 1
            else:
                unsupported.append({"request_id": result.request_id, "claim": claim.text, "reason": reason})
    return {
        "n_results": len(results),
        "n_results_without_evidence": sum(not r.evidence for r in results),
        "n_claims": n,
        "n_supported": supported,
        "claim_support_rate": _rate(supported, n),
        "by_citation_kind": {
            k: {"n": c["n"], "supported": c["supported"], "rate": _rate(c["supported"], c["n"])}
            for k, c in sorted(by_kind.items())
        },
        "unsupported": unsupported,
    }


# --------------------------------------------------------------------------- KG evidence coverage

_PORT_FACT = re.compile(r"ports\[(\d+)\]\.(connector|power_kw)")


def _value(x: Any) -> Any:
    return getattr(x, "value", x)


def _same(a: Any, b: Any) -> bool:
    a, b = _value(a), _value(b)
    if isinstance(a, int | float) and isinstance(b, int | float) and not isinstance(a, bool):
        return abs(float(a) - float(b)) <= 1e-6
    return a is not None and a == b


def displayed_stop_facts(result: PlanResult) -> list[tuple[str, list[Evidence]]] | None:
    """The recommended itinerary's displayed charging-stop facts, each with the facts of THIS
    result's own KG evidence (`kg:` ids in `result.evidence`) that support it. None when there is
    nothing displayed: no recommended plan, or a direct trip with no charging stop.

    Displayed facts per stop (docs/TRADEOFFS.md, evidence coverage): the station name; the chosen
    connector (some KG port of the station has it); the chosen power (one KG port has both that
    connector and that power, so two ports are never combined); and each selected amenity (a KG
    `name` or `category` fact about it). This is the evaluation's own count from the saved
    PlanResult, independent of the ranker's coverage (which also sees the candidates).
    """
    if result.status != "ok" or not result.alternatives:
        return None
    stops = result.alternatives[0].itinerary.stops
    if not stops:
        return None
    kg = [e for e in result.evidence if e.evidence_id.startswith(KG_PREFIX)]
    shown: list[tuple[str, list[Evidence]]] = []
    for stop in stops:
        facts = [e for e in kg if e.subject_id == stop.station_id]
        shown.append(("name", [e for e in facts if e.field == "name" and _same(e.value, stop.station_name)]))
        ports: dict[str, dict[str, Evidence]] = {}
        for e in facts:
            if m := _PORT_FACT.fullmatch(e.field):
                ports.setdefault(m.group(1), {})[m.group(2)] = e
        same_connector = [
            p for p in ports.values() if "connector" in p and _same(p["connector"].value, stop.connector)
        ]
        shown.append(("connector", [p["connector"] for p in same_connector]))
        shown.append(
            ("power", [p["power_kw"] for p in same_connector
                       if "power_kw" in p and _same(p["power_kw"].value, stop.port_power_kw)])
        )
        for amenity_id in stop.amenity_ids:
            shown.append(
                ("amenity", [e for e in kg if e.subject_id == amenity_id and e.field in ("name", "category")])
            )
    return shown


def kg_evidence_coverage_item(result: PlanResult) -> float | None:
    """Share of the recommended itinerary's displayed charging-stop facts (displayed_stop_facts)
    that THIS result's own KG evidence supports. None when there is nothing to cover."""
    shown = displayed_stop_facts(result)
    if shown is None:
        return None
    return sum(bool(support) for _kind, support in shown) / len(shown)


def kg_claim_counts_item(result: PlanResult) -> tuple[int, int]:
    """(claims, claims citing at least one `kg:` id) for one result."""
    kinds = [_kind(c) for c in result.claims]
    return len(kinds), sum(k in ("kg", "mixed") for k in kinds)


def kg_evidence(results: Sequence[PlanResult]) -> dict[str, Any]:
    """KG evidence coverage (mean over results with a recommended charging plan) and the share of
    claims that cite the KG, pooled over all claims."""
    cov = [c for c in (kg_evidence_coverage_item(r) for r in results) if c is not None]
    counts = [kg_claim_counts_item(r) for r in results]
    n_claims, n_kg = sum(n for n, _ in counts), sum(k for _, k in counts)
    return {
        "n_results_with_stops": len(cov),
        "kg_evidence_coverage": sum(cov) / len(cov) if cov else None,
        "n_full_coverage": sum(1 for c in cov if c >= 1 - 1e-9),
        "n_claims": n_claims,
        "n_kg_claims": n_kg,
        "kg_claim_share": _rate(n_kg, n_claims),
    }


# --------------------------------------------------------------------------- provenance completeness (#186)

# Required on the provenance of the fact that supports a displayed stop fact. source and retrieved_at
# are what clickless.data.validate requires of every record; source_record_id makes the source
# citable (which AFDC / OSM record). source_updated_at is optional there (None when the source does
# not publish it, e.g. OSM) and is reported as a share, as in validate.provenance_completeness.
PROVENANCE_REQUIRED = ("source", "source_record_id", "retrieved_at")
PROVENANCE_OPTIONAL = ("source_updated_at",)
VALIDATE_REQUIRED = ("source", "retrieved_at")


def _missing_fields(prov: Provenance | None) -> list[str]:
    if prov is None:
        return list(PROVENANCE_REQUIRED + PROVENANCE_OPTIONAL)
    return [f for f in PROVENANCE_REQUIRED + PROVENANCE_OPTIONAL if getattr(prov, f, None) in (None, "")]


def provenance_completeness_item(result: PlanResult) -> dict[str, Any] | None:
    """Provenance of one result's displayed stop facts (displayed_stop_facts); None when nothing is shown.

    A displayed fact counts as complete only when a fact in THIS result's own KG evidence supports
    it AND that fact's provenance has every PROVENANCE_REQUIRED field. A displayed fact with no
    supporting evidence (absent or unresolvable) is never complete: it is counted under
    `missing["evidence"]`. When several facts support it, the one with the fewest missing fields is
    judged.
    """
    shown = displayed_stop_facts(result)
    if shown is None:
        return None
    out: dict[str, Any] = {
        "n_displayed": len(shown),
        "n_with_evidence": 0,
        "n_complete": 0,
        "n_validate_required": 0,
        "n_source_updated_at": 0,
        "missing": dict.fromkeys(("evidence", *PROVENANCE_REQUIRED, *PROVENANCE_OPTIONAL), 0),
    }
    for _kind, support in shown:
        if not support:
            out["missing"]["evidence"] += 1
            continue
        out["n_with_evidence"] += 1
        missing = min((_missing_fields(e.provenance) for e in support), key=len)
        for f in missing:
            out["missing"][f] += 1
        out["n_complete"] += not any(f in PROVENANCE_REQUIRED for f in missing)
        out["n_validate_required"] += not any(f in VALIDATE_REQUIRED for f in missing)
        out["n_source_updated_at"] += "source_updated_at" not in missing
    return out


def provenance_completeness(results: Sequence[PlanResult]) -> dict[str, Any]:
    """Pooled over the displayed stop facts of every result with a recommended charging plan:
    share with complete provenance (PROVENANCE_REQUIRED), share meeting clickless.data.validate's
    required pair, share with source_updated_at, and missing-field counts. A config without evidence
    (A, B, C-noKG by design) gets rate 0 with its denominator, never a silent skip."""
    items = [c for c in (provenance_completeness_item(r) for r in results) if c is not None]
    n = sum(c["n_displayed"] for c in items)
    keys = ("n_with_evidence", "n_complete", "n_validate_required", "n_source_updated_at")
    totals = {k: sum(c[k] for c in items) for k in keys}
    missing = {f: sum(c["missing"][f] for c in items) for f in items[0]["missing"]} if items else {}
    return {
        "n_results": len(results),
        "n_results_with_stops": len(items),
        "n_results_without_evidence": sum(not r.evidence for r in results),
        "n_displayed": n,
        **totals,
        "provenance_completeness": _rate(totals["n_complete"], n),
        "validate_required_share": _rate(totals["n_validate_required"], n),
        "source_updated_at_share": _rate(totals["n_source_updated_at"], n),
        "missing": missing,
    }


# --------------------------------------------------------------------------- freshness labels


def _station_provenance(result: PlanResult, station_id: str, store=None) -> Provenance | None:
    facts = [e for e in result.evidence if e.subject_id == station_id and not e.field.startswith("near[")]
    named = [e for e in facts if e.field == "name"]
    if named or facts:
        return (named or facts)[0].provenance
    if store is not None:
        try:
            return store.station(station_id).provenance
        except KeyError:
            return None
    return None


def stated_stop_labels(explanation: str) -> list[tuple[int, str]]:
    """(recommended stop index, label) for each '  Source: <label>' line under a 'Stop at' line."""
    out, stop_index = [], -1
    for line in explanation.splitlines():
        if line.startswith("Stop at "):
            stop_index += 1
        elif line.startswith("  Source: "):
            out.append((stop_index, line[len("  Source: ") :].strip()))
    return out


def freshness_mislabel_rate(results: Sequence[PlanResult], store=None) -> dict[str, Any]:
    """Share of stated data-status labels that differ from status_label(provenance)."""
    n_labels = n_checked = 0
    mislabeled: list[dict[str, Any]] = []
    unverifiable: list[dict[str, Any]] = []
    for result in results:
        if result.status != "ok" or not result.alternatives:
            continue
        stops = result.alternatives[0].itinerary.stops
        for i, stated in stated_stop_labels(result.explanation):
            n_labels += 1
            if not 0 <= i < len(stops):
                unverifiable.append({"request_id": result.request_id, "label": stated, "reason": "no matching stop"})
                continue
            prov = _station_provenance(result, stops[i].station_id, store)
            if prov is None:
                unverifiable.append(
                    {"request_id": result.request_id, "label": stated, "reason": "station provenance unavailable"}
                )
                continue
            n_checked += 1
            expected = status_label(prov)
            if stated != expected:
                mislabeled.append(
                    {
                        "request_id": result.request_id,
                        "station_id": stops[i].station_id,
                        "stated": stated,
                        "expected": expected,
                    }
                )
    return {
        "n_labels": n_labels,
        "n_checked": n_checked,
        "n_mislabeled": len(mislabeled),
        "freshness_mislabel_rate": _rate(len(mislabeled), n_checked),
        "n_unverifiable": len(unverifiable),
        "mislabeled": mislabeled,
        "unverifiable": unverifiable,
    }
