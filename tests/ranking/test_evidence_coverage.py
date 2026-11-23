"""Tests for the Config C evidence-coverage calculation."""

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from clickless.contracts import (
    Candidate,
    DataStatus,
    Evidence,
    NearbyAmenity,
    Provenance,
)
from clickless.ranking.evidence_coverage import itinerary_evidence_coverage
from clickless.ranking.weighted_ranker import WeightedRanker


def _fact(subject_id: str, field: str, value: object) -> Evidence:
    """Create one readable synthetic KG fact for coverage tests."""

    return Evidence(
        evidence_id=f"kg:{subject_id}:{field}",
        subject_id=subject_id,
        field=field,
        value=value,
        provenance=Provenance(
            source="SYNTHETIC",
            source_record_id=subject_id,
            retrieved_at=datetime(2026, 11, 15, tzinfo=timezone.utc),
            status=DataStatus.STATIC,
        ),
    )


def _candidate_for_displayed_stop(pipe_a, stop, evidence):
    """Build the enriched candidate that produced one displayed stop."""

    displayed_amenities = [
        NearbyAmenity(
            amenity=next(amenity for amenity in pipe_a.store.amenities if amenity.amenity_id == amenity_id),
            distance_m=100.0,
        )
        for amenity_id in stop.amenity_ids
    ]

    return Candidate(
        station=pipe_a.store.station(stop.station_id),
        route_index=0,
        along_route_km=stop.along_route_km,
        offroute_km=0.0,
        amenities=displayed_amenities,
        evidence=evidence,
    )


def test_full_coverage_when_every_displayed_fact_matches(pipe_a, demo_text):
    """Matching station, port, and amenity facts produce full coverage."""

    plan_result = pipe_a.run(demo_text, request_id="evidence-full")
    itinerary = plan_result.alternatives[0].itinerary
    stop = itinerary.stops[0]

    evidence = [
        _fact(stop.station_id, "name", stop.station_name),
        _fact(stop.station_id, "ports[0].connector", stop.connector.value),
        _fact(stop.station_id, "ports[0].power_kw", stop.port_power_kw),
    ]

    for amenity_id in stop.amenity_ids:
        amenity = next(amenity for amenity in pipe_a.store.amenities if amenity.amenity_id == amenity_id)
        evidence.append(_fact(amenity_id, "name", amenity.name))

    candidate = _candidate_for_displayed_stop(pipe_a, stop, evidence)

    assert itinerary_evidence_coverage(itinerary, [candidate]) == 1.0


def test_power_must_match_the_selected_connector_port(pipe_a, demo_text):
    """Power from a different port must not support the selected port."""

    plan_result = pipe_a.run(demo_text, request_id="evidence-port-pair")
    itinerary = plan_result.alternatives[0].itinerary
    stop = itinerary.stops[0]

    evidence = [
        _fact(stop.station_id, "name", stop.station_name),
        _fact(stop.station_id, "ports[0].connector", stop.connector.value),
        # The numeric power matches, but it belongs to another port.
        _fact(stop.station_id, "ports[1].power_kw", stop.port_power_kw),
    ]

    for amenity_id in stop.amenity_ids:
        amenity = next(amenity for amenity in pipe_a.store.amenities if amenity.amenity_id == amenity_id)
        evidence.append(_fact(amenity_id, "name", amenity.name))

    candidate = _candidate_for_displayed_stop(pipe_a, stop, evidence)

    total_displayed_facts = 3 + len(stop.amenity_ids)
    supported_facts = 2 + len(stop.amenity_ids)

    assert itinerary_evidence_coverage(
        itinerary,
        [candidate],
    ) == pytest.approx(supported_facts / total_displayed_facts)


def test_power_checks_every_port_with_the_selected_connector(
    pipe_a,
    demo_text,
):
    """A later same-connector port may contain the selected charging power."""

    plan_result = pipe_a.run(
        demo_text,
        request_id="evidence-multiple-same-connector-ports",
    )
    itinerary = plan_result.alternatives[0].itinerary
    stop = itinerary.stops[0]
    different_power_kw = (stop.port_power_kw or 0.0) + 1.0

    evidence = [
        _fact(stop.station_id, "name", stop.station_name),
        _fact(stop.station_id, "ports[0].connector", stop.connector.value),
        _fact(stop.station_id, "ports[0].power_kw", different_power_kw),
        _fact(stop.station_id, "ports[1].connector", stop.connector.value),
        _fact(stop.station_id, "ports[1].power_kw", stop.port_power_kw),
    ]

    for amenity_id in stop.amenity_ids:
        amenity = next(amenity for amenity in pipe_a.store.amenities if amenity.amenity_id == amenity_id)
        evidence.append(_fact(amenity_id, "name", amenity.name))

    candidate = _candidate_for_displayed_stop(pipe_a, stop, evidence)

    assert itinerary_evidence_coverage(itinerary, [candidate]) == 1.0


def test_direct_itinerary_has_full_empty_evidence_coverage(pipe_a):
    """A trip with no displayed charging facts has nothing unsupported."""

    plan_result = pipe_a.run(
        "Phoenix to Flagstaff in my comet with 90%",
        request_id="evidence-direct-trip",
    )
    itinerary = plan_result.alternatives[0].itinerary

    assert itinerary.stops == []
    assert itinerary_evidence_coverage(itinerary, []) == 1.0


@pytest.mark.parametrize(
    ("missing_field", "expected_coverage"),
    [
        (None, 1.0),
        ("category", 4 / 5),
        ("wheelchair", 4 / 5),
    ],
)
def test_unnamed_accessible_amenity_uses_displayed_facts(
    pipe_a,
    demo_text,
    missing_field,
    expected_coverage,
):
    """An unnamed accessible amenity is backed by category and wheelchair facts."""

    plan_result = pipe_a.run(
        demo_text,
        request_id="evidence-unnamed-amenity",
    )
    original_itinerary = plan_result.alternatives[0].itinerary
    original_stop = original_itinerary.stops[0]
    amenity_id = original_stop.amenity_ids[0]
    original_amenity = next(amenity for amenity in pipe_a.store.amenities if amenity.amenity_id == amenity_id)
    unnamed_amenity = original_amenity.model_copy(update={"name": None, "wheelchair": "yes"})
    stop_with_one_amenity = original_stop.model_copy(update={"amenity_ids": [amenity_id]})
    itinerary_with_unnamed_amenity = original_itinerary.model_copy(update={"stops": [stop_with_one_amenity]})

    evidence = [
        _fact(
            original_stop.station_id,
            "name",
            original_stop.station_name,
        ),
        _fact(
            original_stop.station_id,
            "ports[0].connector",
            original_stop.connector.value,
        ),
        _fact(
            original_stop.station_id,
            "ports[0].power_kw",
            original_stop.port_power_kw,
        ),
        # A null name is not a displayed fact and must not earn credit.
        _fact(amenity_id, "name", None),
    ]
    amenity_facts = [
        _fact(amenity_id, "category", unnamed_amenity.category.value),
        _fact(amenity_id, "wheelchair", "yes"),
    ]
    evidence.extend(fact for fact in amenity_facts if fact.field != missing_field)
    candidate = Candidate(
        station=pipe_a.store.station(original_stop.station_id),
        route_index=0,
        along_route_km=original_stop.along_route_km,
        offroute_km=0.0,
        amenities=[
            NearbyAmenity(
                amenity=unnamed_amenity,
                distance_m=100.0,
            )
        ],
        evidence=evidence,
    )

    assert itinerary_evidence_coverage(
        itinerary_with_unnamed_amenity,
        [candidate],
    ) == pytest.approx(expected_coverage)


def test_missing_candidate_has_no_evidence_credit(pipe_a, demo_text):
    """An itinerary cannot receive evidence credit without an enriched candidate."""

    plan_result = pipe_a.run(demo_text, request_id="evidence-missing")
    itinerary = plan_result.alternatives[0].itinerary

    assert itinerary_evidence_coverage(itinerary, []) == 0.0


def test_evidence_aware_ranker_applies_configured_credit(
    pipe_a,
    demo_text,
    cfg_a,
):
    """Full KG coverage receives the configured five-minute credit."""

    plan_result = pipe_a.run(demo_text, request_id="evidence-ranker")
    itinerary = plan_result.alternatives[0].itinerary
    request = plan_result.request
    stop = itinerary.stops[0]

    assert request is not None

    evidence = [
        _fact(stop.station_id, "name", stop.station_name),
        _fact(stop.station_id, "ports[0].connector", stop.connector.value),
        _fact(stop.station_id, "ports[0].power_kw", stop.port_power_kw),
    ]

    for amenity_id in stop.amenity_ids:
        amenity = next(amenity for amenity in pipe_a.store.amenities if amenity.amenity_id == amenity_id)
        evidence.append(_fact(amenity_id, "name", amenity.name))

    candidate = _candidate_for_displayed_stop(pipe_a, stop, evidence)
    ranker = WeightedRanker(
        cfg_a["tradeoffs"],
        use_evidence=True,
    )

    cost_without_candidate_evidence = ranker.rank(
        [itinerary],
        request,
    )[0][1]
    cost_with_candidate_evidence = ranker.rank_with_evidence(
        [itinerary],
        request,
        [candidate],
    )[0][1]

    assert cost_without_candidate_evidence.evidence_credit_min == 0.0
    configured_evidence_credit_min = cfg_a["tradeoffs"]["evidence"]["evidence_credit_min"]

    assert cost_with_candidate_evidence.evidence_credit_min == pytest.approx(configured_evidence_credit_min)
    assert cost_with_candidate_evidence.total_min == pytest.approx(
        cost_without_candidate_evidence.total_min - configured_evidence_credit_min
    )


def test_non_evidence_ranker_ignores_candidates_and_profile_credit(
    pipe_a,
    demo_text,
    cfg_a,
):
    """Config A/B must not receive evidence credit through the richer method."""

    plan_result = pipe_a.run(
        demo_text,
        request_id="evidence-disabled-ranker",
    )
    itinerary = plan_result.alternatives[0].itinerary
    request = plan_result.request
    stop = itinerary.stops[0]

    assert request is not None

    evidence = [
        _fact(stop.station_id, "name", stop.station_name),
        _fact(
            stop.station_id,
            "ports[0].connector",
            stop.connector.value,
        ),
        _fact(
            stop.station_id,
            "ports[0].power_kw",
            stop.port_power_kw,
        ),
    ]
    candidate = _candidate_for_displayed_stop(
        pipe_a,
        stop,
        evidence,
    )

    tradeoffs_config = deepcopy(cfg_a["tradeoffs"])
    active_profile_name = request.tradeoff_profile or tradeoffs_config["default_profile"]

    # Simulate a future tuning change. A non-evidence ranker must still force
    # this credit to zero rather than silently adopting the profile value.
    tradeoffs_config["profiles"][active_profile_name]["evidence_credit_min"] = 99.0

    ranker = WeightedRanker(
        tradeoffs_config,
        use_evidence=False,
    )
    ordinary_cost = ranker.rank(
        [itinerary],
        request,
    )[0][1]
    candidate_aware_call_cost = ranker.rank_with_evidence(
        [itinerary],
        request,
        [candidate],
    )[0][1]

    assert ordinary_cost.evidence_credit_min == 0.0
    assert candidate_aware_call_cost.evidence_credit_min == 0.0
    assert candidate_aware_call_cost == ordinary_cost
