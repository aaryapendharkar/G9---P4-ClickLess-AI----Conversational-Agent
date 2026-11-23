"""Measure how completely KG evidence supports an itinerary's displayed facts.

Config C may prefer a well-supported charging plan over an otherwise similar
plan whose displayed details have incomplete KG evidence. This module calculates
that support independently from the ranking formula so it can be tested without
changing itinerary costs.
"""

from __future__ import annotations

import re

from ..contracts import Amenity, Candidate, ChargingStop, Evidence, Itinerary

_PORT_FIELD_PATTERN = re.compile(r"ports\[(?P<port_index>\d+)\]\.(?P<attribute>connector|power_kw)")


def itinerary_evidence_coverage(
    itinerary: Itinerary,
    candidates: list[Candidate],
) -> float:
    """Return the supported share of facts displayed by an itinerary.

    Each charging stop can display its station name, chosen connector, chosen
    charging power, and selected amenities. Named amenities display their name;
    unnamed amenities display their category and, when applicable, wheelchair
    accessibility. A fact counts as supported only when the candidate has KG
    evidence whose value matches the displayed value.

    Returning zero for missing candidates or missing evidence means Config C
    never receives credit for unsupported information.
    """

    # A direct itinerary displays no charging-stop facts, so it has nothing
    # unsupported. Treat that empty set as fully covered rather than penalizing
    # a driver for correctly needing no charging stop.
    if not itinerary.stops:
        return 1.0

    candidates_by_station_id = {candidate.station.station_id: candidate for candidate in candidates}

    supported_fact_count = 0
    displayed_fact_count = 0

    for stop in itinerary.stops:
        candidate = candidates_by_station_id.get(stop.station_id)

        # The station name is always displayed for a charging stop.
        displayed_fact_count += 1
        if candidate is not None and _has_matching_evidence(
            candidate.evidence,
            subject_id=stop.station_id,
            field="name",
            expected_value=stop.station_name,
        ):
            supported_fact_count += 1

        # Connector and charging power are separate displayed facts. However,
        # power receives support only when it belongs to the same KG port as
        # the selected connector. This prevents two different ports from being
        # combined into one fictional charging option.
        displayed_fact_count += 2
        if candidate is not None:
            connector_supported, power_supported = _matching_port_support(
                candidate.evidence,
                stop,
            )
            supported_fact_count += int(connector_supported)
            supported_fact_count += int(power_supported)

        for amenity_id in stop.amenity_ids:
            amenity = _find_amenity(candidate, amenity_id)

            # Preserve the previous fail-closed behavior when an itinerary
            # references an amenity absent from its enriched candidate.
            if amenity is None:
                displayed_fact_count += 1
                continue

            # Keep ranking coverage aligned with the driver-facing label. A
            # named amenity is shown by name. An unnamed one is shown by
            # category, with accessibility included when the label says so.
            for field, expected_value in _displayed_amenity_facts(amenity):
                displayed_fact_count += 1
                if _has_matching_evidence(
                    candidate.evidence,
                    subject_id=amenity_id,
                    field=field,
                    expected_value=expected_value,
                ):
                    supported_fact_count += 1

    if displayed_fact_count == 0:
        return 1.0

    return supported_fact_count / displayed_fact_count


def _displayed_amenity_facts(amenity: Amenity) -> list[tuple[str, object]]:
    """Return the evidence facts represented by an amenity's display label."""

    if amenity.name:
        return [("name", amenity.name)]

    facts: list[tuple[str, object]] = [("category", amenity.category.value)]
    if amenity.wheelchair == "yes":
        facts.append(("wheelchair", "yes"))
    return facts


def _matching_port_support(
    evidence: list[Evidence],
    stop: ChargingStop,
) -> tuple[bool, bool]:
    """Return connector and power support from matching KG ports.

    A station may expose the same connector at several power levels. Connector
    support requires any matching port, while power support requires one port
    whose connector and power both match the selected charging option.
    """

    port_facts: dict[str, dict[str, Evidence]] = {}

    for fact in evidence:
        if fact.subject_id != stop.station_id:
            continue

        field_match = _PORT_FIELD_PATTERN.fullmatch(fact.field)
        if field_match is None:
            continue

        port_index = field_match.group("port_index")
        attribute = field_match.group("attribute")
        port_facts.setdefault(port_index, {})[attribute] = fact

    connector_supported = False

    for facts_for_one_port in port_facts.values():
        connector_fact = facts_for_one_port.get("connector")

        if connector_fact is None or connector_fact.value != stop.connector.value:
            continue

        connector_supported = True
        power_fact = facts_for_one_port.get("power_kw")

        if power_fact is not None and stop.port_power_kw is not None and power_fact.value == stop.port_power_kw:
            return True, True

    return connector_supported, False


def _has_matching_evidence(
    evidence: list[Evidence],
    *,
    subject_id: str,
    field: str,
    expected_value: object,
) -> bool:
    """Return whether an evidence fact exactly supports a displayed value."""

    return any(
        fact.subject_id == subject_id and fact.field == field and fact.value == expected_value for fact in evidence
    )


def _find_amenity(
    candidate: Candidate | None,
    amenity_id: str,
) -> Amenity | None:
    """Find the full amenity record selected for a displayed charging stop."""

    if candidate is None:
        return None

    return next(
        (
            nearby_amenity.amenity
            for nearby_amenity in candidate.amenities
            if nearby_amenity.amenity.amenity_id == amenity_id
        ),
        None,
    )
