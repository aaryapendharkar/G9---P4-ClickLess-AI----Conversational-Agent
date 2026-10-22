"""Configurations B/C ranker: sort by minutes-equivalent cost (C adds evidence credit). Owner: Hriday."""

from __future__ import annotations

from typing import Any

from ..contracts import Itinerary, PlanCost, TripRequest
from .cost import plan_cost


class WeightedRanker:
    def __init__(self, tradeoffs_cfg: dict[str, Any], use_evidence: bool = False) -> None:
        self.cfg = tradeoffs_cfg
        self.use_evidence = use_evidence

    def evidence_coverage(self, itinerary: Itinerary) -> float:
        """Share of stop facts with complete provenance. TODO(Hriday+Aarya) for Config C."""
        return 0.0

    def rank(self, itineraries: list[Itinerary], request: TripRequest) -> list[tuple[Itinerary, PlanCost]]:
        ev_w = self.cfg["evidence"]["evidence_credit_min"] if self.use_evidence else None
        scored = [
            (it, plan_cost(it, request, self.cfg, self.evidence_coverage(it) if self.use_evidence else 0.0, ev_w))
            for it in itineraries
        ]
        return sorted(scored, key=lambda x: (x[1].total_min, len(x[0].stops), x[0].itinerary_id))
