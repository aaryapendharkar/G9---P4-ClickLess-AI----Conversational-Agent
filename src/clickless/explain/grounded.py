"""Configuration C: grounded LLM explanation (proposal §III-E). Owner: Deepti.

Plan:
  1. build an evidence pack ONLY from verified itinerary fields, PlanCost / Alternative deltas
     and linked Evidence (each with an id + data-status label)
  2. prompt the LLM to write short claims, each tagged with the evidence ids it uses
  3. post-check: drop/repair any claim without valid ids or with a number not in the pack;
     an unsupported station can never appear (station names are whitelisted from the itinerary)
  4. return (text, claims) -- Ian's FActScore-style metric scores the claims
Ablation "C without grounding" = free-form LLM text from the same itinerary.
"""

from __future__ import annotations

from ..contracts import Claim, PlanResult


class GroundedLLMExplainer:
    def __init__(self, store, llm_cfg: dict) -> None:
        self.store = store
        self.llm_cfg = llm_cfg

    def explain(self, result: PlanResult) -> tuple[str, list[Claim]]:
        raise NotImplementedError("TODO(Deepti): grounded explanation with evidence-id claims")
