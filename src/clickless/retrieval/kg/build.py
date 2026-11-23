"""Load a DataStore into an RDF graph (Config C knowledge graph). Owner: Manas.

Decisions: ADR 0001 (rdflib + SPARQL), ADR 0015 (what enrichment reads back as Evidence).
Install with `pip install -e ".[kg]"`; rdflib is imported only when a graph is built.

Nodes: one per Station, ChargingPort, Network, Amenity and Vehicle, one cc:Source per provenance
record (stations, amenities, and each derived Station-Amenity distance), and one cc:Proximity per
Station NEAR Amenity pair within `amenity_radius_m`.

Conventions the competency queries rely on (docs/COMPETENCY_QUESTIONS.md):
  * cc:near edges only for amenities within `amenity_radius_m` (400 m, as configs/retrieval.yaml);
    the distance is the same haversine value corridor retrieval puts in NearbyAmenity.distance_m;
  * unknown wheelchair -> no cc:wheelchair triple; unknown port power -> no cc:powerKw triple;
    nothing else that is unknown gets a triple either (no network, no opening hours, ...);
  * native connectors only via cc:supports, documented adapters only via cc:supportsViaAdapter
    (no RDFS materialization in the stored graph, otherwise CQ5 returns nothing).

Runtime Config C builds a route-scoped graph from the corridor candidates; competency queries and
offline analysis can still call ``build_graph(store)`` for a complete graph. This follows ADR
0001's fallback to keep request-time spatial scope outside the KG after the full snapshot measured
7.2 million triples and about 7 GB of additional memory (#115).

TODO(Manas): if a complete serialized KG is needed for offline analysis, write ``kg.ttl`` during
the snapshot build and record its hash in the manifest. Runtime enrichment does not require it.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING
from urllib.parse import quote

from ...common.geo import EARTH_RADIUS_KM, haversine_km
from ...config import repo_path
from ...contracts import Amenity, DataStatus, Evidence, PlanResult, Provenance, Station

if TYPE_CHECKING:  # rdflib is an optional extra
    from rdflib import Graph, URIRef

CC_NS = "https://example.org/clickless#"
DEFAULT_ONTOLOGY = "src/clickless/retrieval/kg/ontology.ttl"
DEFAULT_AMENITY_RADIUS_M = 400.0  # proposal §II; must equal configs/retrieval.yaml amenity_radius_m

_GRID_DEG = 0.01  # spatial bucket for the NEAR join (~1.1 km of latitude)
_M_PER_DEG_LAT = math.radians(1) * EARTH_RADIUS_KM * 1000


def _local(identifier: str) -> str:
    """Contract id -> URI-safe local name ('osm:node/1#toilets' -> 'osm%3Anode%2F1%23toilets')."""
    return quote(identifier, safe="")


def _uri(local_name: str) -> URIRef:
    from rdflib import URIRef

    return URIRef(CC_NS + local_name)


def station_uri(station_id: str) -> URIRef:
    return _uri(f"station_{_local(station_id)}")


def amenity_uri(amenity_id: str) -> URIRef:
    return _uri(f"amenity_{_local(amenity_id)}")


def vehicle_uri(vehicle_id: str) -> URIRef:
    return _uri(f"vehicle_{_local(vehicle_id)}")


def port_uri(station_id: str, index: int) -> URIRef:
    return _uri(f"station_{_local(station_id)}_port_{index}")


def proximity_uri(station_id: str, amenity_id: str) -> URIRef:
    return _uri(f"near_{_local(station_id)}_{_local(amenity_id)}")


def explanation_uri(request_id: str) -> URIRef:
    return _uri(f"explanation_{_local(request_id)}")


def claim_uri(request_id: str, index: int) -> URIRef:
    """Zero-padded, so ORDER BY ?claim is the claims' order in the explanation."""
    return _uri(f"explanation_{_local(request_id)}_claim_{index:04d}")


def _source_uri(kind: str, identifier: str) -> URIRef:
    return _uri(f"source_{kind}_{_local(identifier)}")


def _decimal(value: float) -> Decimal:
    """Exact round trip: float(Decimal(repr(x))) == x, without binary float noise in the Turtle."""
    return Decimal(repr(float(value)))


# ---------------------------------------------------------------------------------- NEAR join


def near_pairs(
    stations: Iterable[Station], amenities: Iterable[Amenity], radius_m: float
) -> Iterator[tuple[Station, Amenity, float]]:
    """Every (station, amenity, distance_m) with haversine distance <= radius_m.

    A grid prefilter (cells of _GRID_DEG) keeps this linear in the snapshot size; the exact test is
    the same `haversine_km(st.lat, st.lon, a.lat, a.lon) * 1000 <= radius` as corridor retrieval,
    so both give the same pairs and the same distances.
    """
    grid: dict[tuple[int, int], list[Amenity]] = defaultdict(list)
    for a in amenities:
        grid[(math.floor(a.lat / _GRID_DEG), math.floor(a.lon / _GRID_DEG))].append(a)
    dlat = 1.05 * radius_m / _M_PER_DEG_LAT  # 5 % margin: the cells only need to be a superset
    for st in stations:
        cos_lat = max(math.cos(math.radians(min(89.0, abs(st.lat) + dlat))), 1e-6)
        dlon = dlat / cos_lat
        rows = range(math.floor((st.lat - dlat) / _GRID_DEG), math.floor((st.lat + dlat) / _GRID_DEG) + 1)
        cols = range(math.floor((st.lon - dlon) / _GRID_DEG), math.floor((st.lon + dlon) / _GRID_DEG) + 1)
        found = []
        for r in rows:
            for c in cols:
                for a in grid.get((r, c), ()):
                    d = haversine_km(st.lat, st.lon, a.lat, a.lon) * 1000
                    if d <= radius_m:
                        found.append((a.amenity_id, a, d))
        for _, a, d in sorted(found, key=lambda x: x[0]):
            yield st, a, d


def derived_provenance(station: Station, amenity: Amenity) -> Provenance:
    """Provenance of a value we computed from a station and an amenity record (e.g. a distance).

    retrieved_at = the later input; status = the least static input (runtime > cached > static).
    """
    order = [DataStatus.STATIC, DataStatus.CACHED, DataStatus.RUNTIME]
    sp, ap = station.provenance, amenity.provenance

    def utc(d: datetime) -> datetime:  # a naive time is read as UTC, so naive and aware compare
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)

    return Provenance(
        source="DERIVED",
        source_record_id=f"haversine({station.station_id},{amenity.amenity_id})",
        retrieved_at=max(sp.retrieved_at, ap.retrieved_at, key=utc),
        status=max(sp.status, ap.status, key=order.index),
    )


# ---------------------------------------------------------------------------------- the graph


def _add_source(g: Graph, node: URIRef, kind: str, identifier: str, prov: Provenance) -> None:
    from rdflib import RDF, XSD, Literal

    from . import vocab as cc

    src = _source_uri(kind, identifier)
    g.add((node, cc.hasProvenance, src))
    g.add((src, RDF.type, cc.Source))
    g.add((src, cc.sourceName, Literal(prov.source)))
    g.add((src, cc.retrievedAt, Literal(prov.retrieved_at)))
    g.add((src, cc.dataStatus, Literal(prov.status.value)))
    if prov.source_record_id is not None:
        g.add((src, cc.sourceRecordId, Literal(prov.source_record_id)))
    if prov.source_url is not None:
        g.add((src, cc.sourceUrl, Literal(prov.source_url, datatype=XSD.anyURI)))
    if prov.source_updated_at is not None:
        g.add((src, cc.sourceUpdatedAt, Literal(prov.source_updated_at)))


def _add_station(g: Graph, st: Station) -> None:
    from rdflib import RDF, RDFS, Literal

    from . import vocab as cc

    node = station_uri(st.station_id)
    g.add((node, RDF.type, cc.Station))
    g.add((node, cc.identifier, Literal(st.station_id)))
    g.add((node, RDFS.label, Literal(st.name)))
    g.add((node, cc.access, Literal(st.access)))
    g.add((node, cc.status, Literal(st.status.value)))
    if st.network is not None:
        network = _uri(f"network_{_local(st.network)}")
        g.add((node, cc.operatedBy, network))
        g.add((network, RDF.type, cc.Network))
        g.add((network, RDFS.label, Literal(st.network)))
    for i, port in enumerate(st.ports):
        p = port_uri(st.station_id, i)
        g.add((node, cc.hasPort, p))
        g.add((p, RDF.type, cc.ChargingPort))
        g.add((p, cc.portIndex, Literal(i)))
        g.add((p, cc.hasConnector, cc.connector(port.connector.value)))
        g.add((p, cc.chargerLevel, Literal(port.level.value)))
        g.add((p, cc.portCount, Literal(port.count)))
        if port.power_kw is not None:
            g.add((p, cc.powerKw, Literal(_decimal(port.power_kw))))
    _add_source(g, node, "station", st.station_id, st.provenance)


def _add_amenity(g: Graph, a: Amenity) -> None:
    from rdflib import RDF, RDFS, Literal

    from . import vocab as cc

    node = amenity_uri(a.amenity_id)
    g.add((node, RDF.type, cc.Amenity))
    g.add((node, cc.identifier, Literal(a.amenity_id)))
    g.add((node, cc.amenityCategory, Literal(a.category.value)))
    if a.name is not None:
        g.add((node, RDFS.label, Literal(a.name)))
    if a.wheelchair != "unknown":  # unknown is the absence of a tag, never a value (proposal §II)
        g.add((node, cc.wheelchair, Literal(a.wheelchair)))
    if a.opening_hours is not None:
        g.add((node, cc.openingHours, Literal(a.opening_hours)))
    _add_source(g, node, "amenity", a.amenity_id, a.provenance)


def _add_near(g: Graph, st: Station, a: Amenity, distance_m: float) -> None:
    from rdflib import RDF, Literal

    from . import vocab as cc

    s, am = station_uri(st.station_id), amenity_uri(a.amenity_id)
    link = proximity_uri(st.station_id, a.amenity_id)
    g.add((s, cc.near, am))
    g.add((link, RDF.type, cc.Proximity))
    g.add((link, cc.fromStation, s))
    g.add((link, cc.toAmenity, am))
    g.add((link, cc.distanceM, Literal(_decimal(distance_m))))
    _add_source(g, link, "near", f"{st.station_id}|{a.amenity_id}", derived_provenance(st, a))


def _add_vehicles(g: Graph, vehicles) -> None:
    from rdflib import RDF, RDFS, Literal

    from . import vocab as cc

    for v in vehicles:
        node = vehicle_uri(v.vehicle_id)
        g.add((node, RDF.type, cc.Vehicle))
        g.add((node, cc.identifier, Literal(v.vehicle_id)))
        g.add((node, RDFS.label, Literal(" ".join(str(x) for x in (v.model_year, v.make, v.model, v.variant) if x))))
        for c in v.native_connectors:
            g.add((node, cc.supports, cc.connector(c.value)))
        for c in v.adapters:
            g.add((node, cc.supportsViaAdapter, cc.connector(c.value)))


_NEAR_FIELD = "near["


def _evidence_source(g: Graph, evidence: Evidence) -> URIRef | None:
    """The cc:Source node of the record an Evidence fact comes from (ADR 0015 §2, §4), or None if
    the graph has no such source. A station-amenity distance comes from the DERIVED proximity
    source; every other fact from its subject's station or amenity record."""
    from rdflib import RDF

    from . import vocab as cc

    if evidence.field.startswith(_NEAR_FIELD) and "]." in evidence.field:
        amenity_id = evidence.field[len(_NEAR_FIELD) : evidence.field.index("].")]
        source = _source_uri("near", f"{evidence.subject_id}|{amenity_id}")
    elif (station_uri(evidence.subject_id), RDF.type, cc.Station) in g:
        source = _source_uri("station", evidence.subject_id)
    elif (amenity_uri(evidence.subject_id), RDF.type, cc.Amenity) in g:
        source = _source_uri("amenity", evidence.subject_id)
    else:
        return None
    return source if (source, RDF.type, cc.Source) in g else None


def add_explanation(g: Graph, result: PlanResult) -> URIRef:
    """Write `result`'s explanation into `g` for CQ9 and return its node.

    cc:Explanation cc:hasClaim each cc:Claim (text as rdfs:label, URIs in claim order). A claim is
    cc:supportedBy the source of each id in `Claim.supported_by` that resolves in
    `result.evidence` (ADR 0015: a kg: id supports a claim only if that result carries it). Ids that
    don't resolve, and `verified:` ids for numbers planning computed, add no source link.
    """
    from rdflib import RDF, RDFS, Literal

    from . import vocab as cc

    node = explanation_uri(result.request_id)
    g.add((node, RDF.type, cc.Explanation))
    g.add((node, cc.identifier, Literal(result.request_id)))
    by_id = {e.evidence_id: e for e in result.evidence}
    for i, claim in enumerate(result.claims):
        c = claim_uri(result.request_id, i)
        g.add((node, cc.hasClaim, c))
        g.add((c, RDF.type, cc.Claim))
        g.add((c, RDFS.label, Literal(claim.text)))
        for evidence_id in claim.supported_by:
            evidence = by_id.get(evidence_id)
            source = None if evidence is None else _evidence_source(g, evidence)
            if source is not None:
                g.add((c, cc.supportedBy, source))
    return node


def build_graph(
    store,
    ontology_path: str = DEFAULT_ONTOLOGY,
    *,
    amenity_radius_m: float = DEFAULT_AMENITY_RADIUS_M,
    stations: Iterable[Station] | None = None,
    amenities: Iterable[Amenity] | None = None,
) -> Graph:
    """Build either the complete KG or a caller-selected record subgraph.

    Leaving ``stations`` and ``amenities`` as ``None`` preserves the complete
    graph used by competency queries and offline snapshot work.

    Config C runtime enrichment passes only the stations and amenities already
    selected for the current route. This keeps request-time memory proportional
    to the corridor candidate set instead of the full national snapshot.
    """

    try:
        from rdflib import Graph
    except ImportError as error:
        raise ImportError('the Config C knowledge graph needs the kg extra: pip install -e ".[kg]"') from error

    graph = Graph()
    graph.bind("cc", CC_NS)
    graph.parse(repo_path(ontology_path), format="turtle")

    selected_stations = sorted(
        store.stations.values() if stations is None else stations,
        key=lambda station: station.station_id,
    )
    selected_amenities = sorted(
        store.amenities if amenities is None else amenities,
        key=lambda amenity: amenity.amenity_id,
    )

    for station in selected_stations:
        _add_station(graph, station)

    for amenity in selected_amenities:
        _add_amenity(graph, amenity)

    for station, amenity, distance_m in near_pairs(
        selected_stations,
        selected_amenities,
        amenity_radius_m,
    ):
        _add_near(
            graph,
            station,
            amenity,
            distance_m,
        )

    # The curated vehicle collection is tiny and is needed by competency
    # queries, so keeping it in both complete and route-scoped graphs has
    # negligible cost and preserves the established ontology shape.
    _add_vehicles(
        graph,
        sorted(
            store.vehicles.values(),
            key=lambda vehicle: vehicle.vehicle_id,
        ),
    )

    return graph
