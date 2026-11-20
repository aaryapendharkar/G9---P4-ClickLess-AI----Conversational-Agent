"""Grounding metrics on hand-built PlanResults: one test per claim-support rule, plus freshness labels.

Rules (src/evaluation/metrics/grounding.py docstring, agreed on PR #66 / ADR 0015 section 5):
  1 per-result kg: lookup   2 verified: ids resolve against the PlanResult   3 empty -> unsupported
  4 unknown values never support specifics   5 A/B (evidence == []) are scored on verified: claims
"""

import ast
from datetime import datetime, timezone
from pathlib import Path

import pytest

from clickless.contracts import (
    Alternative,
    ChargingStop,
    Claim,
    Connector,
    DataStatus,
    Evidence,
    Itinerary,
    PlanCost,
    PlanResult,
    Provenance,
)
from clickless.data.provenance import status_label
from evaluation.metrics.grounding import check_claim, claim_support, freshness_mislabel_rate, stated_accessibility

ROOT = Path(__file__).resolve().parents[2]
RETRIEVED = datetime(2026, 11, 1, tzinfo=timezone.utc)
CACHED = Provenance(
    source="AFDC",
    source_record_id="ST-1",
    retrieved_at=RETRIEVED,
    source_updated_at=datetime(2026, 9, 30, tzinfo=timezone.utc),
    status=DataStatus.CACHED,
)
STALE = CACHED.model_copy(update={"source_updated_at": datetime(2024, 1, 1, tzinfo=timezone.utc)})
DERIVED = CACHED.model_copy(update={"source": "DERIVED", "source_record_id": "haversine(ST-1,AM-1)"})
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


def fact(subject, field, value, prov=CACHED):
    return Evidence(evidence_id=f"kg:{subject}:{field}", subject_id=subject, field=field, value=value, provenance=prov)


def stop(sid="ST-1", kw=150.0):
    return ChargingStop(
        station_id=sid,
        station_name=sid,
        connector=Connector.CCS1,
        port_power_kw=kw,
        along_route_km=60.0,
        detour_min=2.0,
        arrive_soc_pct=25.0,
        depart_soc_pct=80.0,
        charge_min_est=20.0,
    )


def alternative(*stops, label="Recommended"):
    itin = Itinerary(
        itinerary_id=label,
        request_id="r1",
        vehicle_id="t",
        stops=list(stops),
        legs=[],
        total_drive_min=120.0,
        total_charge_min=20.0,
        total_detour_min=2.0,
        arrival_soc_pct=20.0,
    )
    return Alternative(label=label, itinerary=itin, cost=COST)


def result(*claims, request_id="r1", status="ok", alternatives=None, evidence=(), explanation=""):
    alts = [alternative(stop())] if alternatives is None else alternatives
    return PlanResult(
        request_id=request_id,
        config="C",
        status=status,
        alternatives=alts,
        evidence=list(evidence),
        claims=list(claims),
        explanation=explanation,
    )


def claim(*ids, text="A fact."):
    return Claim(text=text, supported_by=list(ids))


def reason(c, res):
    return check_claim(c, res)


# --------------------------------------------------------------------------- rule 1: per-result kg lookup


def test_kg_id_supports_only_through_its_own_result():
    name = fact("ST-1", "name", "Hub")
    own = result(claim("kg:ST-1:name"), evidence=[name])
    other = result(claim("kg:ST-1:name"), request_id="r2", evidence=[])  # same id, but not in r2's evidence
    assert reason(own.claims[0], own) is None
    assert "not in this result's evidence" in reason(other.claims[0], other)
    # aggregated: r1's evidence never leaks into r2 -> 1 of 2 supported
    m = claim_support([own, other])
    assert (m["n_claims"], m["n_supported"]) == (2, 1)
    assert m["claim_support_rate"] == pytest.approx(1 / 2, rel=1e-6)


def test_kg_id_missing_from_evidence_is_unsupported():
    res = result(claim("kg:ST-1:ports[0].connector"), evidence=[fact("ST-1", "name", "Hub")])
    assert "not in this result's evidence" in reason(res.claims[0], res)


# --------------------------------------------------------------------------- rule 2: verified ids


@pytest.mark.parametrize(
    "evidence_id",
    [
        "verified:r1:arrival",
        "verified:r1:cost",
        "verified:r1:stop:0",  # alternatives[0].itinerary.stops[0]
        "verified:r1:alternative:1",  # alternatives[1]
    ],
)
def test_verified_ids_that_resolve(evidence_id):
    res = result(alternatives=[alternative(stop()), alternative(stop("ST-2"), label="Fastest")])
    assert reason(claim(evidence_id), res) is None


@pytest.mark.parametrize(
    ("evidence_id", "why"),
    [
        ("verified:r2:arrival", "another request"),  # request_id must match
        ("verified:r1:stop:1", "no such stop"),  # only one stop in the recommendation
        ("verified:r1:alternative:0", ">= 1"),  # index 0 is the recommendation
        ("verified:r1:alternative:2", "no such alternative"),  # only alternatives[0..1]
        ("verified:r1:stop", "malformed"),
        ("verified:r1:stop:-1", "malformed"),
        ("verified:r1:stop:01", "malformed"),
        ("verified:r1:stop:x", "malformed"),
        ("verified:r1:legs:0", "malformed"),
        ("verified:r1:arrival:0", "malformed"),
    ],
)
def test_verified_ids_that_do_not_resolve(evidence_id, why):
    res = result(alternatives=[alternative(stop()), alternative(stop("ST-2"), label="Fastest")])
    assert why in reason(claim(evidence_id), res)


def test_stop_ids_refer_to_the_recommendation_only():
    # alternatives[1] has two stops, the recommendation one: stop:1 does not resolve
    res = result(alternatives=[alternative(stop()), alternative(stop("A"), stop("B"), label="Fastest")])
    assert "no such stop" in reason(claim("verified:r1:stop:1"), res)


@pytest.mark.parametrize("evidence_id", ["verified:r1:arrival", "verified:r1:cost"])
def test_arrival_and_cost_need_a_recommendation(evidence_id):
    res = result(alternatives=[])
    assert "no recommendation" in reason(claim(evidence_id), res)


@pytest.mark.parametrize("status", ["infeasible", "needs_clarification", "rejected_incomplete", "error"])
def test_verified_ids_only_valid_when_status_is_ok(status):
    res = result(status=status)  # still has alternatives, but the status is not ok
    assert "not ok" in reason(claim("verified:r1:arrival"), res)


def test_kg_ids_are_not_tied_to_status():
    # a relaxation result is "infeasible" but carries the shown relaxation's evidence (#77)
    res = result(claim("kg:ST-1:name"), status="infeasible", alternatives=[], evidence=[fact("ST-1", "name", "Hub")])
    assert reason(res.claims[0], res) is None


# --------------------------------------------------------------------------- rule 3: empty / unknown prefix


def test_empty_supported_by_is_unsupported():
    assert reason(claim(), result()) == "empty supported_by"


def test_other_prefix_is_unsupported():
    assert "unknown evidence id prefix" in reason(claim("osm:node/1"), result())


def test_one_bad_citation_makes_the_claim_unsupported():
    res = result(evidence=[fact("ST-1", "name", "Hub")])
    assert reason(claim("kg:ST-1:name", "verified:r1:arrival"), res) is None
    assert "not in this result's evidence" in reason(claim("kg:ST-1:name", "kg:ST-9:name"), res)


# --------------------------------------------------------------------------- rule 4: unknown values


def test_unknown_power_cannot_support_a_stated_power():
    res = result(evidence=[fact("ST-1", "ports[0].power_kw", None), fact("ST-2", "ports[0].power_kw", 150.0)])
    assert "unknown power" in reason(claim("kg:ST-1:ports[0].power_kw", text="Charges at 150 kW."), res)
    assert "unknown power" in reason(claim("kg:ST-1:ports[0].power_kw", text="Up to 62.5kW here."), res)
    # citing it for what it does say is fine
    assert reason(claim("kg:ST-1:ports[0].power_kw", text="The source does not report power."), res) is None
    # a known power supports the number
    assert reason(claim("kg:ST-2:ports[0].power_kw", text="Charges at 150 kW."), res) is None


def test_unknown_power_on_a_verified_stop_cannot_support_a_stated_power():
    res = result(alternatives=[alternative(stop(kw=None))])
    assert "unknown power" in reason(claim("verified:r1:stop:0", text="Stop at ST-1 (150 kW)."), res)
    assert reason(claim("verified:r1:stop:0", text="Stop at ST-1 (unknown power)."), res) is None


# Wheelchair values per ADR 0011: yes / limited / no / unknown. A claim is supported by a cited
# wheelchair fact only when the value matches what the claim states; unknown supports no statement.
ACCESSIBLE = "The restroom is wheelchair accessible."
NOT_ACCESSIBLE = "The restroom is not accessible."
LIMITED = "The restroom has limited wheelchair accessibility."
NO_STATEMENT = "Restroom accessibility unknown."


@pytest.mark.parametrize(
    ("value", "text", "supported"),
    [
        # "accessible" only by yes
        ("yes", ACCESSIBLE, True),
        ("limited", ACCESSIBLE, False),
        ("no", ACCESSIBLE, False),
        ("unknown", ACCESSIBLE, False),
        (None, ACCESSIBLE, False),
        # "not accessible" only by no
        ("no", NOT_ACCESSIBLE, True),
        ("yes", NOT_ACCESSIBLE, False),
        ("limited", NOT_ACCESSIBLE, False),
        ("unknown", NOT_ACCESSIBLE, False),
        (None, NOT_ACCESSIBLE, False),
        # "limited" only by limited
        ("limited", LIMITED, True),
        ("yes", LIMITED, False),
        ("no", LIMITED, False),
        ("unknown", LIMITED, False),
        (None, LIMITED, False),
        # a claim that states no accessibility value is not contradicted by any value
        ("unknown", NO_STATEMENT, True),
        (None, NO_STATEMENT, True),
    ],
)
def test_wheelchair_value_must_match_the_stated_accessibility(value, text, supported):
    res = result(evidence=[fact("AM-1", "wheelchair", value)])
    got = reason(claim("kg:AM-1:wheelchair", text=text), res)
    if supported:
        assert got is None
    else:
        assert f"wheelchair={value} cannot support" in got


@pytest.mark.parametrize(
    ("text", "stated"),
    [
        ("Wheelchair accessible restroom.", "yes"),
        ("Wheelchair-accessible restroom.", "yes"),
        ("The entrance is accessible.", "yes"),
        ("The restroom is not accessible.", "no"),
        ("The restroom isn't wheelchair accessible.", "no"),
        ("The restroom is inaccessible.", "no"),
        ("Limited wheelchair access to the restroom.", "limited"),
        ("The restroom is partially accessible.", "limited"),
        ("Partial accessibility only.", "limited"),
        ("Restroom accessibility unknown.", None),
        ("Open 24 hours.", None),
        ("Limited opening hours.", None),  # "limited" without an accessibility word states nothing
    ],
)
def test_stated_accessibility_reading(text, stated):
    assert stated_accessibility(text) == stated


# --------------------------------------------------------------------------- rule 5: A/B have no evidence


def test_configs_without_evidence_are_scored_on_verified_claims():
    res = result(
        claim("verified:r1:stop:0", text="Stop at ST-1: charge 25% -> 80%."),
        claim("verified:r1:arrival"),
        claim("verified:r1:cost"),
        evidence=[],
    )
    m = claim_support([res])
    assert m["n_results_without_evidence"] == 1
    assert (m["n_claims"], m["n_supported"], m["claim_support_rate"]) == (3, 3, 1.0)
    assert m["by_citation_kind"] == {"verified": {"n": 3, "supported": 3, "rate": 1.0}}
    # a kg: citation in an evidence-free result cannot resolve (rule 1)
    assert "not in this result's evidence" in reason(claim("kg:ST-1:name"), res)


def test_claim_support_aggregate_hand_counted():
    ok = result(
        claim("verified:r1:arrival"),  # supported
        claim("kg:ST-1:name"),  # supported
        claim("kg:ST-1:name", "verified:r1:cost"),  # supported (mixed)
        claim(),  # empty -> unsupported
        claim("verified:r1:stop:3"),  # no such stop -> unsupported
        evidence=[fact("ST-1", "name", "Hub")],
    )
    m = claim_support([ok, result(request_id="r2")])  # r2 has no claims
    # 3 supported of 5
    assert (m["n_results"], m["n_claims"], m["n_supported"]) == (2, 5, 3)
    assert m["claim_support_rate"] == pytest.approx(3 / 5, rel=1e-6)
    assert m["by_citation_kind"]["none"] == {"n": 1, "supported": 0, "rate": 0.0}
    assert m["by_citation_kind"]["mixed"] == {"n": 1, "supported": 1, "rate": 1.0}
    assert m["by_citation_kind"]["verified"] == {"n": 2, "supported": 1, "rate": 0.5}
    assert [u["reason"] for u in m["unsupported"]] == [
        "empty supported_by",
        "verified:r1:stop:3: no such stop in the recommendation",
    ]


def test_no_claims_gives_no_rate():
    assert claim_support([])["claim_support_rate"] is None


# --------------------------------------------------------------------------- freshness labels


def explained(label, evidence, stops=None):
    text = "\n".join(
        ["Stop at ST-1 (CCS1, 150 kW): charge 25% -> 80% in about 20 min (estimate).", f"  Source: {label}"]
    )
    return result(alternatives=[alternative(*(stops or [stop()]))], evidence=evidence, explanation=text)


def test_correct_label_from_the_results_own_station_facts():
    # the DERIVED distance fact is skipped; the station's name fact carries the record provenance
    ev = [fact("ST-1", "near[AM-1].distance_m", 120.0, DERIVED), fact("ST-1", "name", "Hub")]
    expected = status_label(CACHED)  # "AFDC, cached snapshot (source updated 2026-09-30)"
    m = freshness_mislabel_rate([explained(expected, ev)])
    assert (m["n_labels"], m["n_checked"], m["n_mislabeled"], m["freshness_mislabel_rate"]) == (1, 1, 0, 0.0)


def test_stale_record_labelled_fresh_is_a_mislabel():
    # retrieved 2026-11-01, source updated 2024-01-01: older than 365 days -> "; may be out of date"
    ev = [fact("ST-1", "name", "Hub", STALE)]
    stated = "AFDC, cached snapshot (source updated 2024-01-01)"  # the staleness note is missing
    m = freshness_mislabel_rate([explained(stated, ev), explained(status_label(STALE), ev)])
    # 1 mislabel of 2 checked
    assert (m["n_checked"], m["n_mislabeled"]) == (2, 1)
    assert m["freshness_mislabel_rate"] == pytest.approx(1 / 2, rel=1e-6)
    assert m["mislabeled"][0]["expected"] == "AFDC, cached snapshot (source updated 2024-01-01); may be out of date"


def test_runtime_vs_cached_status_is_a_mislabel():
    ev = [fact("ST-1", "name", "Hub", CACHED.model_copy(update={"status": DataStatus.RUNTIME}))]
    m = freshness_mislabel_rate([explained(status_label(CACHED), ev)])  # says "cached", record is live
    assert m["freshness_mislabel_rate"] == 1.0


def test_labels_without_provenance_or_stop_are_unverifiable():
    no_facts = explained(status_label(CACHED), [])
    extra = result(evidence=[fact("ST-1", "name", "Hub")], explanation="  Source: AFDC, cached snapshot")
    m = freshness_mislabel_rate([no_facts, extra])
    assert (m["n_labels"], m["n_checked"], m["n_unverifiable"]) == (2, 0, 2)
    assert m["freshness_mislabel_rate"] is None
    assert {u["reason"] for u in m["unverifiable"]} == {"station provenance unavailable", "no matching stop"}


def test_template_labels_match_status_label_end_to_end(pipe_a, demo_text, store):
    """Config A's template with provenance labels on, checked against the fixture store (no KG evidence)."""
    from clickless.explain.templates import TemplateExplainer

    pipe_a.explainer = TemplateExplainer(store, provenance_labels=True)
    res = pipe_a.run(demo_text, request_id="fresh-1")
    m = freshness_mislabel_rate([res], store=store)
    assert m["n_labels"] == len(res.alternatives[0].itinerary.stops) > 0
    assert (m["n_checked"], m["n_mislabeled"], m["freshness_mislabel_rate"]) == (m["n_labels"], 0, 0.0)


# --------------------------------------------------------------------------- independence


def test_grounding_metric_does_not_import_the_system_under_test():
    forbidden = ("clickless.planning", "clickless.ranking", "clickless.retrieval", "clickless.pipeline")
    tree = ast.parse((ROOT / "src/evaluation/metrics/grounding.py").read_text(encoding="utf-8"))
    imported = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not [m for m in imported if m.startswith(forbidden)]
