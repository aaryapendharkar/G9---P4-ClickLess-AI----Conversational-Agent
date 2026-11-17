"""Select KG evidence for the alternatives shown to the driver.

Retrieval attaches evidence to every candidate it considers. A ``PlanResult``
must be smaller and safer: it carries facts only for stops and amenities that
appear in the selected alternatives. Keeping this selection independent from
the explainer also lets saved results be evaluated offline.
"""

from __future__ import annotations

from ..contracts import Alternative, Candidate, Evidence


def collect_shown_evidence(
    alternatives: list[Alternative],
    candidates: list[Candidate],
) -> list[Evidence]:
    """Return each fact used by a shown stop once, in display order.

    The order follows ADR 0015: alternatives, then stops, then the station
    facts followed by each displayed amenity's distance and facts. Configs
    without KG enrichment naturally return an empty list because their
    candidates have no evidence.
    """

    candidates_by_station_id = {
        candidate.station.station_id: candidate for candidate in candidates
    }
    selected_evidence: list[Evidence] = []
    included_evidence_ids: set[str] = set()

    def include_once(facts: list[Evidence]) -> None:
        """Append facts while preserving their producer-defined order."""

        for fact in facts:
            if fact.evidence_id not in included_evidence_ids:
                included_evidence_ids.add(fact.evidence_id)
                selected_evidence.append(fact)

    for alternative in alternatives:
        for stop in alternative.itinerary.stops:
            candidate = candidates_by_station_id.get(stop.station_id)
            if candidate is None:
                # The verifier handles unknown stations. Evidence selection
                # must not invent a fact when no enriched candidate exists.
                continue

            station_facts = [
                fact
                for fact in candidate.evidence
                if fact.subject_id == stop.station_id
                and not fact.field.startswith("near[")
            ]
            include_once(station_facts)

            for amenity_id in stop.amenity_ids:
                distance_field = f"near[{amenity_id}].distance_m"
                distance_facts = [
                    fact
                    for fact in candidate.evidence
                    if fact.subject_id == stop.station_id
                    and fact.field == distance_field
                ]
                amenity_facts = [
                    fact
                    for fact in candidate.evidence
                    if fact.subject_id == amenity_id
                ]
                include_once(distance_facts)
                include_once(amenity_facts)

    return selected_evidence
