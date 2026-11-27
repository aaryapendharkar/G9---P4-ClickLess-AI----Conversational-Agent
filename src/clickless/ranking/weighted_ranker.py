"""Configurations B/C ranker using minutes-equivalent trade-off costs.

Configuration B uses ordinary weighted costs. Configuration C can additionally
reward itineraries whose displayed station, port, and amenity facts are
supported by KG evidence.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..contracts import Candidate, Itinerary, PlanCost, TripRequest
from .cost import plan_cost
from .evidence_coverage import itinerary_evidence_coverage


class WeightedRanker:
    def __init__(
        self,
        tradeoffs_cfg: dict[str, Any],
        use_evidence: bool = False,
        amenity_credit_config: Mapping[str, Any] | None = None,
    ) -> None:
        self.cfg = tradeoffs_cfg
        self.use_evidence = use_evidence
        self.amenity_credit_config = (
            dict(amenity_credit_config)
            if amenity_credit_config is not None
            else None
        )

    def rank(
        self,
        itineraries: list[Itinerary],
        request: TripRequest,
    ) -> list[tuple[Itinerary, PlanCost]]:
        """Rank without candidate evidence.

        This remains the standard Ranker interface used by Config B and by the
        Config C ablation where KG enrichment is disabled.
        """

        coverage_by_itinerary_id: dict[str, float] = {}
        return self._rank_with_coverages(
            itineraries,
            request,
            coverage_by_itinerary_id,
        )

    def rank_with_evidence(
        self,
        itineraries: list[Itinerary],
        request: TripRequest,
        candidates: list[Candidate],
    ) -> list[tuple[Itinerary, PlanCost]]:
        """Rank Config C itineraries using their matching enriched candidates."""

        # Config A/B rankers must ignore candidate evidence even if a pipeline
        # wrapper calls this richer interface. This prevents a future nonzero
        # profile default from leaking Config C's evidence credit into them.
        if not self.use_evidence:
            return self.rank(
                itineraries,
                request,
            )

        coverage_by_itinerary_id = {
            itinerary.itinerary_id: itinerary_evidence_coverage(
                itinerary,
                candidates,
            )
            for itinerary in itineraries
        }

        return self._rank_with_coverages(
            itineraries,
            request,
            coverage_by_itinerary_id,
        )

    def _rank_with_coverages(
        self,
        itineraries: list[Itinerary],
        request: TripRequest,
        coverage_by_itinerary_id: dict[str, float],
    ) -> list[tuple[Itinerary, PlanCost]]:
        """Apply costs and deterministic tie-breaking to every itinerary."""

        evidence_credit_weight = (
            self.cfg["evidence"]["evidence_credit_min"]
            if self.use_evidence
            else 0.0
        )

        scored_itineraries = [
            (
                itinerary,
                plan_cost(
                    itinerary,
                    request,
                    self.cfg,
                    evidence_coverage=coverage_by_itinerary_id.get(
                        itinerary.itinerary_id,
                        0.0,
                    ),
                    evidence_credit_min=evidence_credit_weight,
                    amenity_credit_config=self.amenity_credit_config,
                ),
            )
            for itinerary in itineraries
        ]

        return sorted(
            scored_itineraries,
            key=lambda scored: (
                scored[1].total_min,
                len(scored[0].stops),
                scored[0].itinerary_id,
            ),
        )
