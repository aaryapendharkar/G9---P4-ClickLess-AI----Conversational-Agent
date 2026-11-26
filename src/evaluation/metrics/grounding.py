"""Grounding metrics (proposal §V-C). Owner: Ian.

  * atomic-claim evidence support, FActScore-style (target >= 95 %) over PlanResult.claims
  * data-status (freshness) mislabeling rate (target 0 %)
  * provenance completeness (uses clickless.data.validate output) -- TODO
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
