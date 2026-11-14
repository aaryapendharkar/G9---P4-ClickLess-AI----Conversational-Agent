"""The cc: terms the KG code writes and reads (ontology.ttl v0.2). Owner: Manas.

Imports rdflib, so only build.py / enrich.py import this module, and only inside functions.
Every term here must be declared in ontology.ttl (tests/retrieval/test_kg_enrichment.py checks).
"""

from __future__ import annotations

from rdflib import URIRef

from .build import CC_NS


def _t(name: str) -> URIRef:
    return URIRef(CC_NS + name)


# classes
Station, ChargingPort, Network, Connector = _t("Station"), _t("ChargingPort"), _t("Network"), _t("Connector")
Vehicle, Amenity, Source, Proximity = _t("Vehicle"), _t("Amenity"), _t("Source"), _t("Proximity")

# object properties
hasPort, hasConnector, operatedBy = _t("hasPort"), _t("hasConnector"), _t("operatedBy")
supports, supportsViaAdapter, near = _t("supports"), _t("supportsViaAdapter"), _t("near")
fromStation, toAmenity, hasProvenance = _t("fromStation"), _t("toAmenity"), _t("hasProvenance")

# datatype properties
identifier, access, status = _t("identifier"), _t("access"), _t("status")
portIndex, chargerLevel, powerKw, portCount = _t("portIndex"), _t("chargerLevel"), _t("powerKw"), _t("portCount")
amenityCategory, wheelchair, openingHours = _t("amenityCategory"), _t("wheelchair"), _t("openingHours")
distanceM = _t("distanceM")
sourceName, sourceRecordId, sourceUrl = _t("sourceName"), _t("sourceRecordId"), _t("sourceUrl")
retrievedAt, sourceUpdatedAt, dataStatus = _t("retrievedAt"), _t("sourceUpdatedAt"), _t("dataStatus")

TERMS: tuple[URIRef, ...] = tuple(v for v in dict(globals()).values() if isinstance(v, URIRef))


def connector(value: str) -> URIRef:
    """Connector individual, e.g. 'CCS1' -> cc:CCS1 (declared in ontology.ttl)."""
    return _t(value)


def connector_name(node: URIRef) -> str:
    """cc:CCS1 -> 'CCS1'."""
    text = str(node)
    if not text.startswith(CC_NS):
        raise ValueError(f"not a cc: connector: {node}")
    return text[len(CC_NS) :]
