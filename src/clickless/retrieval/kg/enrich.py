"""Config C: enrich spatially retrieved candidates from the KG. Owner: Manas.

Traversal ADDS compatibility facts, amenities and Evidence to candidates; it never adds stations
that spatial retrieval did not return and never removes any (filters do that). Ablation
"C without KG" = replace this with a no-op.
"""

from __future__ import annotations

from ...contracts import Candidate, TripRequest


class KGEnricher:
    def __init__(self, store, kg_cfg: dict) -> None:
        self.store = store
        self.kg_cfg = kg_cfg

    def enrich(self, candidates: list[Candidate], request: TripRequest) -> list[Candidate]:
        raise NotImplementedError("TODO(Manas): KG traversal -> candidate evidence")
