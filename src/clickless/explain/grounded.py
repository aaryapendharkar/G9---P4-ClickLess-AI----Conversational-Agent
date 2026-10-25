"""Deterministic grounded explanations for Configuration C. Owner: Deepti.

The current shared ``PlanResult`` contract carries verified itinerary/cost fields but does not
carry the KG evidence list produced by the retrieval stage. This implementation therefore uses
only verified PlanResult fields and station provenance already available through DataStore. It
never asks an LLM to invent facts; when a future evidence-aware contract is available, the
private evidence-pack boundary can be replaced without changing the explainer interface.
"""

from __future__ import annotations

from ..contracts import Claim, PlanResult
from ..data import DataStore
from .templates import TemplateExplainer
from .tradeoff_presenter import render_alternative, render_cost_breakdown


class GroundedLLMExplainer:
    """Explain only facts present in the verified result and datastore."""

    def __init__(self, store: DataStore, llm_cfg: dict) -> None:
        self.store = store
        self.llm_cfg = llm_cfg
        self.template_explainer = TemplateExplainer(store, provenance_labels=True)

    @staticmethod
    def _evidence_id(request_id: str, *parts: str) -> str:
        return ":".join(("verified", request_id, *parts))

    def _claims_for_success(self, result: PlanResult) -> list[Claim]:
        if not result.alternatives or result.request is None:
            return []
        recommendation = result.alternatives[0]
        itinerary = recommendation.itinerary
        claims: list[Claim] = []
        for stop_index, stop in enumerate(itinerary.stops):
            evidence_id = self._evidence_id(result.request_id, "stop", str(stop_index))
            claim_text = (
                f"Stop at {stop.station_name}: charge {stop.arrive_soc_pct:.0f}% -> "
                f"{stop.depart_soc_pct:.0f}% in about {stop.charge_min_est:.0f} min (estimate)."
            )
            claims.append(Claim(text=claim_text, supported_by=[evidence_id]))

        arrival_evidence_id = self._evidence_id(result.request_id, "arrival")
        claims.append(
            Claim(
                text=(
                    f"Arrive in {result.request.destination.text} with about "
                    f"{itinerary.arrival_soc_pct:.0f}% after ~{itinerary.total_time_min:.0f} min total."
                ),
                supported_by=[arrival_evidence_id],
            )
        )
        cost_evidence_id = self._evidence_id(result.request_id, "cost")
        claims.append(Claim(text=render_cost_breakdown(recommendation.cost), supported_by=[cost_evidence_id]))
        for alternative_index, alternative in enumerate(result.alternatives[1:], start=1):
            alternative_evidence_id = self._evidence_id(result.request_id, "alternative", str(alternative_index))
            claims.append(
                Claim(
                    text=render_alternative(alternative),
                    supported_by=[alternative_evidence_id],
                )
            )
        return claims

    def explain(self, result: PlanResult) -> tuple[str, list[Claim]]:
        text, _ = self.template_explainer.explain(result)
        if result.status != "ok":
            return text, []
        return text, self._claims_for_success(result)
