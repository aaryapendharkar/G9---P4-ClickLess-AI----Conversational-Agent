"""Config C: enrich spatially retrieved candidates from the KG. Owner: Manas. Format: ADR 0015.

Traversal ADDS Evidence to candidates; it never adds stations that spatial retrieval did not
return and never removes any (filters do that), and it leaves amenities and match scores alone.
Ablation "C without KG" = replace this with a no-op (every Candidate.evidence stays []).

What each candidate gets (`contracts.Evidence`, one atomic, citable fact each; ADR 0015):

  subject_id    field                              value                     provenance
  station_id    name, network, status, access      str (network: str|None)   the station record (AFDC)
  station_id    ports[i].connector / .level        "CCS1" / "dc_fast"        the station record
  station_id    ports[i].power_kw                  float | None              the station record
  station_id    ports[i].count                     int                       the station record
  station_id    near[<amenity_id>].distance_m      float, metres             DERIVED (haversine)
  amenity_id    name, category, opening_hours      str | None                the amenity record (OSM)
  amenity_id    wheelchair                         "yes"|"limited"|"no"|"unknown"   the amenity record

  * evidence_id = "kg:<subject_id>:<field>", e.g. "kg:FIX-ST-004:ports[0].power_kw". It depends
    only on the snapshot, not on the request, so the same fact has the same id in every result.
    Treat it as opaque; read subject_id / field instead of parsing it.
  * Every field above is always present. None means "the source did not report it" (ADR 0010 for
    power): cite it as unknown, never as 0 or as a default. wheelchair "unknown" never counts as
    accessible (proposal §II).
  * Values and provenance are read back from the graph (retrieval/kg/build.py); they equal the
    DataStore record field for field (tests/retrieval/test_kg_enrichment.py checks every one).
  * Order: station facts, ports by index, then for each candidate.amenities entry in its order:
    the distance, then that amenity's facts.
"""

from __future__ import annotations

from ...contracts import Candidate, DataStatus, Evidence, Provenance, TripRequest
from .build import (
    DEFAULT_AMENITY_RADIUS_M,
    DEFAULT_ONTOLOGY,
    amenity_uri,
    build_graph,
    proximity_uri,
    station_uri,
)

EVIDENCE_PREFIX = "kg"


def evidence_id(subject_id: str, field: str) -> str:
    """Stable id of one KG fact: 'kg:<subject_id>:<field>'."""
    return f"{EVIDENCE_PREFIX}:{subject_id}:{field}"


def _text(literal) -> str | None:
    return None if literal is None else str(literal)


class KGEnricher:
    def __init__(self, store, kg_cfg: dict) -> None:
        self.store = store
        self.kg_cfg = kg_cfg
        self._graph = None
        self._station_facts: dict[str, list[Evidence]] = {}
        self._amenity_facts: dict[str, list[Evidence]] = {}
        self._near_facts: dict[tuple[str, str], list[Evidence]] = {}

    @property
    def graph(self):
        """The rdflib graph of the whole store, built on first use (building a C pipeline stays cheap)."""
        if self._graph is None:
            self._graph = build_graph(
                self.store,
                self.kg_cfg.get("ontology", DEFAULT_ONTOLOGY),
                amenity_radius_m=float(self.kg_cfg.get("amenity_radius_m", DEFAULT_AMENITY_RADIUS_M)),
            )
        return self._graph

    def enrich(self, candidates: list[Candidate], request: TripRequest) -> list[Candidate]:
        for c in candidates:
            station_id = c.station.station_id
            facts = list(self.station_evidence(station_id))
            for nearby in c.amenities:
                facts += self.near_evidence(station_id, nearby.amenity.amenity_id)
                facts += self.amenity_evidence(nearby.amenity.amenity_id)
            seen = {e.evidence_id for e in c.evidence}
            for e in facts:
                if e.evidence_id not in seen:
                    seen.add(e.evidence_id)
                    c.evidence.append(e)
        return candidates

    # ------------------------------------------------------------------ facts per subject
    def station_evidence(self, station_id: str) -> list[Evidence]:
        """Station and port facts; [] for a station the KG does not hold (nothing is invented)."""
        if station_id not in self._station_facts:
            self._station_facts[station_id] = self._read_station(station_id)
        return self._station_facts[station_id]

    def amenity_evidence(self, amenity_id: str) -> list[Evidence]:
        if amenity_id not in self._amenity_facts:
            self._amenity_facts[amenity_id] = self._read_amenity(amenity_id)
        return self._amenity_facts[amenity_id]

    def near_evidence(self, station_id: str, amenity_id: str) -> list[Evidence]:
        """The station-amenity distance; [] if the KG has no NEAR link for the pair."""
        key = (station_id, amenity_id)
        if key not in self._near_facts:
            self._near_facts[key] = self._read_near(station_id, amenity_id)
        return self._near_facts[key]

    # ------------------------------------------------------------------ graph reads
    def _provenance(self, node) -> Provenance:
        from . import vocab as cc

        g = self.graph
        src = g.value(node, cc.hasProvenance)
        updated = g.value(src, cc.sourceUpdatedAt)
        return Provenance(
            source=str(g.value(src, cc.sourceName)),
            source_record_id=_text(g.value(src, cc.sourceRecordId)),
            source_url=_text(g.value(src, cc.sourceUrl)),
            retrieved_at=g.value(src, cc.retrievedAt).toPython(),
            source_updated_at=None if updated is None else updated.toPython(),
            status=DataStatus(str(g.value(src, cc.dataStatus))),
        )

    def _read_station(self, station_id: str) -> list[Evidence]:
        from rdflib import RDF, RDFS

        from . import vocab as cc

        g, node = self.graph, station_uri(station_id)
        if (node, RDF.type, cc.Station) not in g:
            return []
        prov = self._provenance(node)

        def fact(field: str, value) -> Evidence:
            eid = evidence_id(station_id, field)
            return Evidence(evidence_id=eid, subject_id=station_id, field=field, value=value, provenance=prov)

        network = g.value(node, cc.operatedBy)
        out = [
            fact("name", _text(g.value(node, RDFS.label))),
            fact("network", None if network is None else _text(g.value(network, RDFS.label))),
            fact("status", _text(g.value(node, cc.status))),
            fact("access", _text(g.value(node, cc.access))),
        ]
        for port in sorted(g.objects(node, cc.hasPort), key=lambda p: int(g.value(p, cc.portIndex).toPython())):
            i = int(g.value(port, cc.portIndex).toPython())
            power = g.value(port, cc.powerKw)
            out += [
                fact(f"ports[{i}].connector", cc.connector_name(g.value(port, cc.hasConnector))),
                fact(f"ports[{i}].level", _text(g.value(port, cc.chargerLevel))),
                fact(f"ports[{i}].power_kw", None if power is None else float(power.toPython())),
                fact(f"ports[{i}].count", int(g.value(port, cc.portCount).toPython())),
            ]
        return out

    def _read_amenity(self, amenity_id: str) -> list[Evidence]:
        from rdflib import RDF, RDFS

        from . import vocab as cc

        g, node = self.graph, amenity_uri(amenity_id)
        if (node, RDF.type, cc.Amenity) not in g:
            return []
        prov = self._provenance(node)
        values = {
            "name": _text(g.value(node, RDFS.label)),
            "category": _text(g.value(node, cc.amenityCategory)),
            "wheelchair": _text(g.value(node, cc.wheelchair)) or "unknown",  # no triple = not tagged
            "opening_hours": _text(g.value(node, cc.openingHours)),
        }
        return [
            Evidence(evidence_id=evidence_id(amenity_id, f), subject_id=amenity_id, field=f, value=v, provenance=prov)
            for f, v in values.items()
        ]

    def _read_near(self, station_id: str, amenity_id: str) -> list[Evidence]:
        from rdflib import RDF

        from . import vocab as cc

        g, link = self.graph, proximity_uri(station_id, amenity_id)
        if (link, RDF.type, cc.Proximity) not in g:
            return []
        field = f"near[{amenity_id}].distance_m"
        return [
            Evidence(
                evidence_id=evidence_id(station_id, field),
                subject_id=station_id,
                field=field,
                value=float(g.value(link, cc.distanceM).toPython()),
                provenance=self._provenance(link),
            )
        ]
