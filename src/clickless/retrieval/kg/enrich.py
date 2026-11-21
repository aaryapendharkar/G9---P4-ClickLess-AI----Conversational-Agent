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

from ...contracts import (
    Amenity,
    Candidate,
    DataStatus,
    Evidence,
    Provenance,
    Station,
    TripRequest,
)
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
        self._graph_scope_key: tuple[
            tuple[str, ...],
            tuple[str, ...],
        ] | None = None
        self._station_facts: dict[str, list[Evidence]] = {}
        self._amenity_facts: dict[str, list[Evidence]] = {}
        self._near_facts: dict[tuple[str, str], list[Evidence]] = {}

    @property
    def graph(self):
        """Return the active RDF graph.

        Direct access before enrichment keeps the historical complete-graph
        behavior used by offline and exploratory callers. Normal Config C
        requests activate a route-scoped graph in ``enrich()`` first.
        """

        if self._graph is None:
            self._graph = build_graph(
                self.store,
                self.kg_cfg.get("ontology", DEFAULT_ONTOLOGY),
                amenity_radius_m=float(
                    self.kg_cfg.get(
                        "amenity_radius_m",
                        DEFAULT_AMENITY_RADIUS_M,
                    )
                ),
            )

        return self._graph

    def _candidate_scope(
        self,
        candidates: list[Candidate],
    ) -> tuple[
        tuple[Station, ...],
        tuple[Amenity, ...],
        tuple[tuple[str, ...], tuple[str, ...]],
    ]:
        """Resolve the candidate scope through the authoritative DataStore.

        Candidate objects normally originate from this store, but resolving by
        ID prevents copied or fabricated candidate records from entering the
        KG. Missing records remain missing and therefore produce no evidence.
        """

        station_ids = {
            candidate.station.station_id
            for candidate in candidates
            if candidate.station.station_id in self.store.stations
        }
        selected_stations = tuple(
            self.store.stations[station_id]
            for station_id in sorted(station_ids)
        )

        amenities_by_id = {
            amenity.amenity_id: amenity
            for amenity in self.store.amenities
        }
        amenity_ids = {
            nearby.amenity.amenity_id
            for candidate in candidates
            for nearby in candidate.amenities
            if nearby.amenity.amenity_id in amenities_by_id
        }
        selected_amenities = tuple(
            amenities_by_id[amenity_id]
            for amenity_id in sorted(amenity_ids)
        )

        scope_key = (
            tuple(station.station_id for station in selected_stations),
            tuple(amenity.amenity_id for amenity in selected_amenities),
        )
        return selected_stations, selected_amenities, scope_key

    def _activate_candidate_graph(
        self,
        candidates: list[Candidate],
    ) -> None:
        """Build the route graph once and reuse it for the same candidate set."""

        stations, amenities, scope_key = self._candidate_scope(candidates)

        if self._graph is not None and self._graph_scope_key == scope_key:
            return

        self._graph = build_graph(
            self.store,
            self.kg_cfg.get("ontology", DEFAULT_ONTOLOGY),
            amenity_radius_m=float(
                self.kg_cfg.get(
                    "amenity_radius_m",
                    DEFAULT_AMENITY_RADIUS_M,
                )
            ),
            stations=stations,
            amenities=amenities,
        )
        self._graph_scope_key = scope_key

        # Evidence objects are derived from the active graph. They must not
        # survive when a later request activates a different route subgraph.
        self._station_facts.clear()
        self._amenity_facts.clear()
        self._near_facts.clear()

    def enrich(
        self,
        candidates: list[Candidate],
        request: TripRequest,
    ) -> list[Candidate]:
        self._activate_candidate_graph(candidates)

        for candidate in candidates:
            station_id = candidate.station.station_id
            facts = list(self.station_evidence(station_id))
            for nearby_amenity in candidate.amenities:
                amenity_id = nearby_amenity.amenity.amenity_id
                facts += self.near_evidence(station_id, amenity_id)
                facts += self.amenity_evidence(amenity_id)

            seen_evidence_ids = {
                evidence.evidence_id
                for evidence in candidate.evidence
            }
            for evidence in facts:
                if evidence.evidence_id not in seen_evidence_ids:
                    seen_evidence_ids.add(evidence.evidence_id)
                    candidate.evidence.append(evidence)
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
