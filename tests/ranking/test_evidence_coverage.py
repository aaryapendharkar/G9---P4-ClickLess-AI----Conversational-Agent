"""Tests for the Config C evidence-coverage calculation."""

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


def _fact(subject_id: str, field: str, value: str | float) -> Evidence:
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
            amenity=next(
                amenity
                for amenity in pipe_a.store.amenities
                if amenity.amenity_id == amenity_id
            ),
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
        amenity = next(
            amenity
            for amenity in pipe_a.store.amenities
            if amenity.amenity_id == amenity_id
        )
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
        amenity = next(
            amenity
            for amenity in pipe_a.store.amenities
            if amenity.amenity_id == amenity_id
        )
        evidence.append(_fact(amenity_id, "name", amenity.name))

    candidate = _candidate_for_displayed_stop(pipe_a, stop, evidence)

    total_displayed_facts = 3 + len(stop.amenity_ids)
    supported_facts = 2 + len(stop.amenity_ids)

    assert itinerary_evidence_coverage(
        itinerary,
        [candidate],
    ) == pytest.approx(supported_facts / total_displayed_facts)


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
        amenity = next(
            amenity
            for amenity in pipe_a.store.amenities
            if amenity.amenity_id == amenity_id
        )
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
    assert cost_with_candidate_evidence.evidence_credit_min == pytest.approx(5.0)
    assert cost_with_candidate_evidence.total_min == pytest.approx(
        cost_without_candidate_evidence.total_min - 5.0
    )
