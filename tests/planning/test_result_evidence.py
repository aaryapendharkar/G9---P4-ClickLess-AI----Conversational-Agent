"""Tests for narrowing candidate evidence to facts shown in a plan result."""

from datetime import datetime, timezone

from clickless.contracts import Candidate, DataStatus, Evidence, Provenance
from clickless.planning.result_evidence import collect_shown_evidence


def _fact(subject_id: str, field: str, value: str | float) -> Evidence:
    """Create one readable synthetic fact for evidence-selection tests."""

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


def test_collect_shown_evidence_keeps_only_displayed_stop_facts(pipe_a, demo_text):
    """Unshown amenities and duplicate alternatives must not enlarge the result."""

    plan_result = pipe_a.run(demo_text, request_id="evidence-selection")
    shown_alternative = plan_result.alternatives[0]
    shown_stop = shown_alternative.itinerary.stops[0]
    shown_amenity_id = shown_stop.amenity_ids[0]
    unshown_amenity_id = "FIX-AM-HIDDEN"

    station_name = _fact(shown_stop.station_id, "name", shown_stop.station_name)
    shown_distance = _fact(
        shown_stop.station_id,
        f"near[{shown_amenity_id}].distance_m",
        100.0,
    )
    shown_amenity_name = _fact(shown_amenity_id, "name", "Shown amenity")
    hidden_distance = _fact(
        shown_stop.station_id,
        f"near[{unshown_amenity_id}].distance_m",
        200.0,
    )
    hidden_amenity_name = _fact(
        unshown_amenity_id,
        "name",
        "Hidden amenity",
    )
    candidate = Candidate(
        station=pipe_a.store.station(shown_stop.station_id),
        route_index=0,
        along_route_km=shown_stop.along_route_km,
        offroute_km=0.0,
        evidence=[
            station_name,
            shown_distance,
            shown_amenity_name,
            hidden_distance,
            hidden_amenity_name,
        ],
    )

    selected = collect_shown_evidence(
        [shown_alternative, shown_alternative],
        [candidate],
    )

    assert [fact.evidence_id for fact in selected] == [
        station_name.evidence_id,
        shown_distance.evidence_id,
        shown_amenity_name.evidence_id,
    ]


def test_collect_shown_evidence_is_empty_without_enrichment(pipe_a, demo_text):
    """Configs A/B keep their established empty-evidence behavior."""

    plan_result = pipe_a.run(demo_text, request_id="evidence-empty")
    shown_stop = plan_result.alternatives[0].itinerary.stops[0]
    candidate_without_evidence = Candidate(
        station=pipe_a.store.station(shown_stop.station_id),
        route_index=0,
        along_route_km=shown_stop.along_route_km,
        offroute_km=0.0,
    )

    assert collect_shown_evidence(
        plan_result.alternatives,
        [candidate_without_evidence],
    ) == []
