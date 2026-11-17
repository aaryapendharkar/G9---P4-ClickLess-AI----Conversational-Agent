"""Deterministic grounded explanations for Configuration C. Owner: Deepti.

KG facts are cited through ``PlanResult.evidence`` when available. Verified itinerary and cost
claims continue to use stable ``verified:<request_id>:...`` ids, which the grounding metric checks
against the result itself. Configurations A/B and the C-without-KG ablation keep their existing
verified-only behavior because their evidence list is empty.
"""

from __future__ import annotations

import re

from ..contracts import Claim, Evidence, PlanResult
from ..data import DataStore
from .templates import TemplateExplainer
from .tradeoff_presenter import render_alternative, render_cost_breakdown

_PORT_FIELD = re.compile(r"ports\[(\d+)\]\.(connector|power_kw)")


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
    def _port_fact_ids(
        result: PlanResult,
        station_id: str,
        connector: str,
        power_kw: float | None,
    ) -> list[str]:
        """Return connector and power facts only when they belong to the same port index."""
        ports: dict[str, dict[str, Evidence]] = {}
        for evidence in result.evidence:
            if evidence.subject_id != station_id:
                continue
            match = _PORT_FIELD.fullmatch(evidence.field)
            if match:
                ports.setdefault(match.group(1), {})[match.group(2)] = evidence
        for fields in ports.values():
            connector_fact = fields.get("connector")
            power_fact = fields.get("power_kw")
            if (
                connector_fact is not None
                and connector_fact.value == connector
                and power_fact is not None
                and power_fact.value == power_kw
            ):
                return [connector_fact.evidence_id, power_fact.evidence_id]
        return []

    @staticmethod
    def _station_name_id(result: PlanResult, station_id: str) -> str | None:
        for evidence in result.evidence:
            if evidence.subject_id == station_id and evidence.field == "name":
                return evidence.evidence_id
        return None

    @staticmethod
    def _amenity_name_ids(result: PlanResult, amenity_ids: list[str]) -> list[str]:
        requested = set(amenity_ids)
        return [
            evidence.evidence_id
            for evidence in result.evidence
            if evidence.subject_id in requested and evidence.field == "name"
        ]

    def _claims_for_success(self, result: PlanResult) -> list[Claim]:
        if not result.alternatives or result.request is None:
            return []

        evidence_by_id = self._evidence_by_id(result)
        recommendation = result.alternatives[0]
        itinerary = recommendation.itinerary
        claims: list[Claim] = []
        for stop_index, stop in enumerate(itinerary.stops):
            verified_stop_id = self._verified_id(result.request_id, "stop", str(stop_index))
            support_ids = [verified_stop_id]
            for evidence_id in [
                self._station_name_id(result, stop.station_id),
                *self._port_fact_ids(result, stop.station_id, stop.connector.value, stop.port_power_kw),
            ]:
                if evidence_id is not None and evidence_id in evidence_by_id:
                    support_ids.append(evidence_id)
            power = f"{stop.port_power_kw:.0f} kW" if stop.port_power_kw else "unknown power"
            claim_text = (
                f"Stop at {stop.station_name} ({stop.connector.value}, {power}): charge "
                f"{stop.arrive_soc_pct:.0f}% -> {stop.depart_soc_pct:.0f}% in about "
                f"{stop.charge_min_est:.0f} min (estimate)."
            )
            claims.append(Claim(text=claim_text, supported_by=support_ids))

            amenity_names = [
                amenity.name
                for amenity in self.store.amenities
                if amenity.amenity_id in stop.amenity_ids and amenity.name
            ]
            amenity_ids = self._amenity_name_ids(result, stop.amenity_ids)
            if amenity_names and amenity_ids:
                claims.append(
                    Claim(
                        text="Nearby (requested): " + ", ".join(amenity_names) + ".",
                        supported_by=amenity_ids,
                    )
                )

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
