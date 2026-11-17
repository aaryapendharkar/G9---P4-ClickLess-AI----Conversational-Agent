"""Deterministic grounded explanations for Configuration C. Owner: Deepti.

KG facts are cited through ``PlanResult.evidence`` when available. Verified itinerary and cost
claims continue to use stable ``verified:<request_id>:...`` ids, which the grounding metric checks
against the result itself. Configurations A/B and the C-without-KG ablation keep their existing
verified-only behavior because their evidence list is empty.
"""

from __future__ import annotations

from ..contracts import Claim, Evidence, PlanResult
from ..data import DataStore
from .templates import TemplateExplainer
from .tradeoff_presenter import render_alternative, render_cost_breakdown


class GroundedLLMExplainer:
    """Explain only verified result fields and evidence carried by that result."""

    def __init__(self, store: DataStore, llm_cfg: dict) -> None:
        self.store = store
        self.llm_cfg = llm_cfg
        self.template_explainer = TemplateExplainer(store, provenance_labels=True)

    @staticmethod
    def _verified_id(request_id: str, *parts: str) -> str:
        return ":".join(("verified", request_id, *parts))

    @staticmethod
    def _evidence_by_id(result: PlanResult) -> dict[str, Evidence]:
        return {evidence.evidence_id: evidence for evidence in result.evidence}

    @staticmethod
    def _kg_fact_ids(
        result: PlanResult,
        station_id: str,
        connector: str,
        power_kw: float | None,
    ) -> list[str]:
        """Find cited KG ids without parsing the opaque id format."""
        facts = [evidence for evidence in result.evidence if evidence.subject_id == station_id]
        ids: list[str] = []
        for evidence in facts:
            if evidence.field.endswith(".connector") and evidence.value == connector:
                ids.append(evidence.evidence_id)
            elif evidence.field.endswith(".power_kw") and evidence.value == power_kw:
                ids.append(evidence.evidence_id)
            elif evidence.field == "name":
                ids.append(evidence.evidence_id)
        return ids

    def _claims_for_success(self, result: PlanResult) -> list[Claim]:
        if not result.alternatives or result.request is None:
            return []

        # Build the lookup even when evidence is empty so duplicate IDs are harmless and every
        # kg: citation below is guaranteed to come from this PlanResult.
        evidence_by_id = self._evidence_by_id(result)
        recommendation = result.alternatives[0]
        itinerary = recommendation.itinerary
        claims: list[Claim] = []
        for stop_index, stop in enumerate(itinerary.stops):
            support_ids = self._kg_fact_ids(
                result,
                stop.station_id,
                stop.connector.value,
                stop.port_power_kw,
            )
            support_ids.append(self._verified_id(result.request_id, "stop", str(stop_index)))
            support_ids = [
                evidence_id
                for evidence_id in support_ids
                if evidence_id in evidence_by_id or evidence_id.startswith("verified:")
            ]
            claim_text = (
                f"Stop at {stop.station_name}: charge {stop.arrive_soc_pct:.0f}% -> "
                f"{stop.depart_soc_pct:.0f}% in about {stop.charge_min_est:.0f} min (estimate)."
            )
            claims.append(Claim(text=claim_text, supported_by=support_ids))

        claims.append(
            Claim(
                text=(
                    f"Arrive in {result.request.destination.text} with about "
                    f"{itinerary.arrival_soc_pct:.0f}% after ~{itinerary.total_time_min:.0f} min total."
                ),
                supported_by=[self._verified_id(result.request_id, "arrival")],
            )
        )
        claims.append(
            Claim(
                text=render_cost_breakdown(recommendation.cost),
                supported_by=[self._verified_id(result.request_id, "cost")],
            )
        )
        for alternative_index, alternative in enumerate(result.alternatives[1:], start=1):
            claims.append(
                Claim(
                    text=render_alternative(alternative),
                    supported_by=[self._verified_id(result.request_id, "alternative", str(alternative_index))],
                )
            )
        return claims

    def explain(self, result: PlanResult) -> tuple[str, list[Claim]]:
        text, _ = self.template_explainer.explain(result)
        if result.status != "ok":
            return text, []
        return text, self._claims_for_success(result)
