"""Integration tests for carrying only displayed KG evidence into PlanResult."""

from datetime import datetime, timezone

import pytest

from clickless.config import load_config
from clickless.contracts import DataStatus, Evidence, PlanResult, Provenance
from clickless.explain.tradeoff_presenter import render_cost_breakdown
from clickless.pipeline import build_pipeline
from clickless.ranking.weighted_ranker import WeightedRanker
from clickless.retrieval.kg.enrich import KGEnricher


class _NoOpEnricher:
    """Represent the Config C ablation where KG evidence is disabled."""

    def enrich(self, candidates, _request):
        return candidates


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


class _RecordingEvidenceRanker:
    """Record which ranking path the pipeline selects, then rank normally."""

    def __init__(self, wrapped_ranker):
        self.wrapped_ranker = wrapped_ranker
        self.standard_rank_calls = 0
        self.evidence_rank_calls = 0
        self.received_candidate_count = 0

    @property
    def fallback_used(self) -> bool:
        """Expose the wrapped ranker's fallback state like production code."""

        return getattr(
            self.wrapped_ranker,
            "fallback_used",
            False,
        )

    def rank(self, itineraries, request):
        """Record use of the ordinary Config A/B ranking interface."""

        self.standard_rank_calls += 1
        return self.wrapped_ranker.rank(
            itineraries,
            request,
        )

    def rank_with_evidence(
        self,
        itineraries,
        request,
        candidates,
    ):
        """Record Config C candidate delivery without changing rank results."""

        self.evidence_rank_calls += 1
        self.received_candidate_count = len(candidates)

        # The dispatch test is about which method receives candidates.
        # Evidence arithmetic is tested separately in test_evidence_coverage.py.
        return self.wrapped_ranker.rank(
            itineraries,
            request,
        )


def _shown_station_ids(alternatives):
    """Return unique displayed station ids in alternative and stop order."""

    station_ids = []
    for alternative in alternatives:
        for stop in alternative.itinerary.stops:
            if stop.station_id not in station_ids:
                station_ids.append(stop.station_id)
    return station_ids


def _shown_subject_ids(alternatives):
    """Return every station and amenity id visible in the alternatives."""

    subject_ids = set()
    for alternative in alternatives:
        for stop in alternative.itinerary.stops:
            subject_ids.add(stop.station_id)
            subject_ids.update(stop.amenity_ids)
    return subject_ids


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


def test_real_kg_evidence_is_limited_to_displayed_plan_subjects(
    pipe_a,
    store,
    demo_text,
):
    """Exercise the real KG producer through the complete planning boundary.

    The core local environment may omit the optional KG dependency. CI installs
    it, so this test runs there instead of replacing KG facts with a stand-in.
    """

    pytest.importorskip("rdflib")
    config_c = load_config("C")
    pipe_a.enricher = KGEnricher(store, config_c["kg"])

    result = pipe_a.run(demo_text, request_id="real-kg-result-evidence")

    assert result.status == "ok"
    assert result.evidence

    evidence_ids = [fact.evidence_id for fact in result.evidence]
    assert len(evidence_ids) == len(set(evidence_ids))

    shown_subject_ids = _shown_subject_ids(result.alternatives)
    assert {fact.subject_id for fact in result.evidence} <= shown_subject_ids

    result_evidence_ids = set(evidence_ids)
    for claim in result.claims:
        cited_kg_ids = {
            evidence_id
            for evidence_id in claim.supported_by
            if evidence_id.startswith("kg:")
        }
        assert cited_kg_ids <= result_evidence_ids


@pytest.mark.parametrize("config_name", ["A", "B"])
def test_configs_without_kg_do_not_build_enricher(config_name, store):
    """Configurations A and B must preserve their empty-evidence boundary."""

    pipeline = build_pipeline(load_config(config_name), store)

    assert pipeline.enricher is None


def test_pipeline_uses_evidence_aware_ranking_after_enrichment(
    pipe_a,
    demo_text,
):
    """An active KG enricher sends enriched candidates to the capable ranker."""

    pipe_a.enricher = _StationNameEnricher()
    recording_ranker = _RecordingEvidenceRanker(pipe_a.ranker)
    pipe_a.ranker = recording_ranker

    result = pipe_a.run(
        demo_text,
        request_id="evidence-ranking-dispatch",
    )

    assert (
        "logreg ranker not trained yet -- fell back to weighted cost"
        in result.notes
    )
    assert "evidence ranking: on" in result.notes
    assert result.status == "ok"
    assert recording_ranker.evidence_rank_calls == 1
    assert recording_ranker.standard_rank_calls == 0
    assert recording_ranker.received_candidate_count > 0


def test_no_kg_ablation_matches_standard_weighted_ranking(
    pipe_a,
    cfg_a,
    demo_text,
):
    """C without KG must produce B-equivalent weighted order and costs."""

    pipe_a.enricher = _NoOpEnricher()
    pipe_a.ranker = WeightedRanker(
        cfg_a["tradeoffs"],
        use_evidence=True,
    )

    ablation_result = pipe_a.run(
        demo_text,
        request_id="no-kg-ablation-comparison",
    )

    pipe_a.enricher = None
    pipe_a.ranker = WeightedRanker(
        cfg_a["tradeoffs"],
        use_evidence=False,
    )

    standard_result = pipe_a.run(
        demo_text,
        request_id="standard-weighted-comparison",
    )

    ablation_stop_sequences = [
        tuple(
            stop.station_id
            for stop in alternative.itinerary.stops
        )
        for alternative in ablation_result.alternatives
    ]
    standard_stop_sequences = [
        tuple(
            stop.station_id
            for stop in alternative.itinerary.stops
        )
        for alternative in standard_result.alternatives
    ]

    assert ablation_result.status == "ok"
    assert standard_result.status == "ok"
    assert ablation_result.evidence == []
    assert ablation_stop_sequences == standard_stop_sequences
    assert [
        alternative.cost
        for alternative in ablation_result.alternatives
    ] == [
        alternative.cost
        for alternative in standard_result.alternatives
    ]
    assert all(
        alternative.cost.evidence_credit_min == 0.0
        for alternative in ablation_result.alternatives
    )
    assert (
        "evidence ranking: off "
        "(no KG evidence on candidates)"
        in ablation_result.notes
    )
    assert not any(
        note.startswith("evidence ranking:")
        for note in standard_result.notes
    )


def test_pipeline_preserves_standard_ranking_without_enrichment(
    pipe_a,
    demo_text,
):
    """Configs without KG enrichment must not receive evidence credit."""

    assert pipe_a.enricher is None

    recording_ranker = _RecordingEvidenceRanker(pipe_a.ranker)
    pipe_a.ranker = recording_ranker

    result = pipe_a.run(
        demo_text,
        request_id="standard-ranking-dispatch",
    )

    assert result.status == "ok"
    assert recording_ranker.standard_rank_calls == 1
    assert recording_ranker.evidence_rank_calls == 0


def test_real_kg_ranking_displays_complete_cost_arithmetic(
    pipe_a,
    store,
    cfg_a,
    demo_text,
):
    """Real KG evidence must affect cost and appear in its explanation."""

    pytest.importorskip("rdflib")
    config_c = load_config("C")
    pipe_a.enricher = KGEnricher(
        store,
        config_c["kg"],
    )
    pipe_a.ranker = WeightedRanker(
        cfg_a["tradeoffs"],
        use_evidence=True,
    )

    result = pipe_a.run(
        demo_text,
        request_id="real-kg-evidence-ranking",
    )

    assert result.status == "ok"
    assert result.evidence

    recommended_cost = result.alternatives[0].cost

    assert recommended_cost.evidence_credit_min > 0.0
    assert render_cost_breakdown(recommended_cost) in result.explanation
    assert "verified evidence" in result.explanation
