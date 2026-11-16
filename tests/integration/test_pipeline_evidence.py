"""Integration tests for carrying only displayed KG evidence into PlanResult."""

from datetime import datetime, timezone

from clickless.contracts import DataStatus, Evidence, PlanResult, Provenance


class _StationNameEnricher:
    """Small KG stand-in that avoids requiring rdflib in the core test suite."""

    def enrich(self, candidates, _request):
        for candidate in candidates:
            station = candidate.station
            candidate.evidence.append(
                Evidence(
                    evidence_id=f"kg:{station.station_id}:name",
                    subject_id=station.station_id,
                    field="name",
                    value=station.name,
                    provenance=Provenance(
                        source="SYNTHETIC",
                        source_record_id=station.station_id,
                        retrieved_at=datetime(
                            2026,
                            11,
                            15,
                            tzinfo=timezone.utc,
                        ),
                        status=DataStatus.STATIC,
                    ),
                )
            )
        return candidates


def _shown_station_ids(alternatives):
    """Return unique displayed station ids in alternative and stop order."""

    station_ids = []
    for alternative in alternatives:
        for stop in alternative.itinerary.stops:
            if stop.station_id not in station_ids:
                station_ids.append(stop.station_id)
    return station_ids


def test_normal_plan_carries_evidence_only_for_shown_stations(pipe_a, demo_text):
    pipe_a.enricher = _StationNameEnricher()

    result = pipe_a.run(demo_text, request_id="normal-result-evidence")

    assert result.status == "ok"
    assert [fact.subject_id for fact in result.evidence] == _shown_station_ids(
        result.alternatives
    )
    assert "FIX-ST-008" not in {fact.subject_id for fact in result.evidence}


def test_successful_relaxation_carries_only_its_shown_evidence(pipe_a, demo_text):
    pipe_a.enricher = _StationNameEnricher()
    parsed = pipe_a.parser.parse(demo_text, "relaxed-result-evidence")
    strict_request = parsed.request.model_copy(update={"max_detour_min": 0.0})
    initial_result = PlanResult(
        request_id=strict_request.request_id,
        config=pipe_a.name,
        status="ok",
        request=strict_request,
    )

    result = pipe_a.plan_request(strict_request, initial_result)

    assert result.status == "infeasible"
    assert result.alternatives == []
    assert result.relaxation is not None
    assert [fact.subject_id for fact in result.evidence] == _shown_station_ids(
        result.relaxation.alternatives
    )


def test_failed_solution_does_not_leak_candidate_evidence(pipe_a, demo_text):
    pipe_a.enricher = _StationNameEnricher()
    parsed = pipe_a.parser.parse(demo_text, "failed-result-evidence")
    impossible_request = parsed.request.model_copy(update={"min_power_kw": 1_000.0})
    initial_result = PlanResult(
        request_id=impossible_request.request_id,
        config=pipe_a.name,
        status="ok",
        request=impossible_request,
    )

    result = pipe_a.plan_request(impossible_request, initial_result)

    assert result.status == "infeasible"
    assert result.relaxation is None
    assert result.evidence == []
