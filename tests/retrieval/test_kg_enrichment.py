"""KG enrichment: the evidence Config C's grounded explanation can cite (issue #52, ADR 0015)."""

import re
from datetime import datetime, timezone
from enum import Enum

import pytest

from clickless.common.geo import haversine_km
from clickless.config import load_config, repo_path
from clickless.contracts import (
    Amenity,
    AmenityCategory,
    Candidate,
    ChargerLevel,
    ChargingPort,
    Connector,
    DataStatus,
    NearbyAmenity,
    Provenance,
    Station,
)
from clickless.data import DataStore
from clickless.pipeline import build_pipeline
from clickless.retrieval.kg.build import (
    DEFAULT_AMENITY_RADIUS_M,
    amenity_uri,
    build_graph,
    derived_provenance,
    near_pairs,
    station_uri,
)
from clickless.retrieval.kg.enrich import KGEnricher, evidence_id
from clickless.retrieval.tfidf_matcher import TfidfMatcher

pytest.importorskip("rdflib")  # CI installs the kg extra; this only skips on a machine without it


def _demo_candidates(pipe_a, text):
    req = pipe_a.parser.parse(text, "kg-test").request
    route = pipe_a.router.route(req.origin, req.destination)
    return pipe_a.retriever.candidates(route, req), req


def _enriched(pipe_a, store, text):
    candidates, req = _demo_candidates(pipe_a, text)
    return KGEnricher(store, {}).enrich(candidates, req)


def _plain(value):
    return value.value if isinstance(value, Enum) else value


def _expected(store, ev):
    """(value, provenance) the DataStore holds for one evidence item."""
    amenities = {a.amenity_id: a for a in store.amenities}
    if ev.subject_id in store.stations:
        st = store.station(ev.subject_id)
        if m := re.fullmatch(r"near\[(.+)\]\.distance_m", ev.field):
            a = amenities[m[1]]
            return haversine_km(st.lat, st.lon, a.lat, a.lon) * 1000, derived_provenance(st, a)
        if m := re.fullmatch(r"ports\[(\d+)\]\.(\w+)", ev.field):
            return _plain(getattr(st.ports[int(m[1])], m[2])), st.provenance
        return _plain(getattr(st, ev.field)), st.provenance
    a = amenities[ev.subject_id]
    return _plain(getattr(a, ev.field)), a.provenance


# ------------------------------------------------------------------ the facts #52 asks for


def test_station_fact_is_citable_with_its_source(pipe_a, store, demo_text):
    camp_verde = next(c for c in _enriched(pipe_a, store, demo_text) if c.station.station_id == "FIX-ST-004")
    facts = {e.evidence_id: e for e in camp_verde.evidence}

    power = facts["kg:FIX-ST-004:ports[0].power_kw"]

    assert (power.subject_id, power.field, power.value) == ("FIX-ST-004", "ports[0].power_kw", 50.0)
    assert facts["kg:FIX-ST-004:ports[0].connector"].value == "CCS1"
    assert power.provenance == store.station("FIX-ST-004").provenance


def test_amenity_fact_and_its_distance_are_citable(pipe_a, store, demo_text):
    camp_verde = next(c for c in _enriched(pipe_a, store, demo_text) if c.station.station_id == "FIX-ST-004")
    facts = {e.evidence_id: e for e in camp_verde.evidence}
    kitchen = next(na for na in camp_verde.amenities if na.amenity.amenity_id == "fix:a05")

    wheelchair = facts["kg:fix:a05:wheelchair"]
    distance = facts["kg:FIX-ST-004:near[fix:a05].distance_m"]

    assert (wheelchair.subject_id, wheelchair.field, wheelchair.value) == ("fix:a05", "wheelchair", "yes")
    assert wheelchair.provenance == kitchen.amenity.provenance
    assert distance.subject_id == "FIX-ST-004" and distance.value == kitchen.distance_m <= 400
    assert distance.provenance.source == "DERIVED"


def test_every_fact_equals_the_datastore_record(pipe_a, store, demo_text):
    evidence = [e for c in _enriched(pipe_a, store, demo_text) for e in c.evidence]

    assert evidence
    for ev in evidence:
        assert ev.evidence_id == evidence_id(ev.subject_id, ev.field)
        assert (ev.value, ev.provenance) == _expected(store, ev), ev.evidence_id


# ------------------------------------------------------------------ unknowns and real provenance


def _osm_like_store(tmp_path):
    afdc = Provenance(
        source="AFDC",
        source_record_id="123456",
        source_url="https://developer.nlr.gov/api/alt-fuel-stations/v1/123456.json",
        retrieved_at=datetime(2026, 11, 10, 8, 0, tzinfo=timezone.utc),
        source_updated_at=datetime(2025, 3, 1, 12, 30, tzinfo=timezone.utc),
        status=DataStatus.CACHED,
    )
    osm = Provenance(
        source="OSM",
        source_record_id="node/42",
        source_url="https://www.openstreetmap.org/node/42",
        retrieved_at=datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc),
        source_updated_at=datetime(2024, 6, 2, tzinfo=timezone.utc),
        status=DataStatus.CACHED,
    )
    station = Station(
        station_id="AFDC-123456",
        name="Unknown Power Plaza",
        lat=35.0,
        lon=-111.0,
        network=None,
        ports=[ChargingPort(connector=Connector.CCS1, level=ChargerLevel.DC_FAST, power_kw=None, count=2)],
        provenance=afdc,
    )
    restroom = Amenity(
        amenity_id="osm:node/42#toilets",
        category=AmenityCategory.RESTROOM,
        lat=35.001,
        lon=-111.0,
        wheelchair="unknown",
        provenance=osm,
    )
    return DataStore([station], [], [restroom], {}, tmp_path), station, restroom


def test_unknown_values_are_explicit_and_never_guessed(tmp_path):
    store, station, restroom = _osm_like_store(tmp_path)
    nearby = NearbyAmenity(amenity=restroom, distance_m=haversine_km(35.0, -111.0, 35.001, -111.0) * 1000)
    candidate = Candidate(station=station, route_index=0, along_route_km=0, offroute_km=0, amenities=[nearby])

    [enriched] = KGEnricher(store, {}).enrich([candidate], request=None)
    facts = {e.evidence_id: e for e in enriched.evidence}

    assert facts["kg:AFDC-123456:ports[0].power_kw"].value is None  # not reported, never 0 or 50 kW
    assert facts["kg:AFDC-123456:network"].value is None
    assert facts["kg:osm:node/42#toilets:wheelchair"].value == "unknown"  # never counts as accessible
    assert facts["kg:osm:node/42#toilets:name"].value is None
    assert facts["kg:osm:node/42#toilets:opening_hours"].value is None
    assert facts["kg:AFDC-123456:ports[0].power_kw"].provenance == station.provenance
    assert facts["kg:osm:node/42#toilets:category"].provenance == restroom.provenance
    distance = facts["kg:AFDC-123456:near[osm:node/42#toilets].distance_m"]
    assert distance.value == nearby.distance_m
    assert distance.provenance.status == DataStatus.CACHED
    assert distance.provenance.retrieved_at == restroom.provenance.retrieved_at  # the later input
    for ev in enriched.evidence:
        assert (ev.value, ev.provenance) == _expected(store, ev), ev.evidence_id


def test_a_station_the_kg_does_not_hold_gets_no_evidence(tmp_path, store):
    _, station, _ = _osm_like_store(tmp_path)
    candidate = Candidate(station=station, route_index=0, along_route_km=0, offroute_km=0)

    assert KGEnricher(store, {}).enrich([candidate], request=None)[0].evidence == []


# ------------------------------------------------------------------ what enrichment must not do


def test_enrichment_only_adds_evidence(pipe_a, store, demo_text):
    candidates, req = _demo_candidates(pipe_a, demo_text)
    before = [c.model_dump(exclude={"evidence"}) for c in candidates]

    enriched = KGEnricher(store, {}).enrich(candidates, req)

    assert [c.model_dump(exclude={"evidence"}) for c in enriched] == before
    assert all(c.evidence for c in enriched)


def test_evidence_is_deterministic_and_unique(pipe_a, store, demo_text):
    first = _enriched(pipe_a, store, demo_text)
    second = _enriched(pipe_a, store, demo_text)

    def dump(candidates):
        return [[e.model_dump() for e in c.evidence] for c in candidates]

    assert dump(first) == dump(second)
    for c in first:
        ids = [e.evidence_id for e in c.evidence]
        assert len(ids) == len(set(ids))


def test_kg_near_links_are_the_corridor_links(pipe_a, store, demo_text):
    assert DEFAULT_AMENITY_RADIUS_M == load_config("C")["retrieval"]["amenity_radius_m"]
    brute_force = {
        (s.station_id, a.amenity_id)
        for s in store.stations.values()
        for a in store.amenities
        if haversine_km(s.lat, s.lon, a.lat, a.lon) * 1000 <= DEFAULT_AMENITY_RADIUS_M
    }
    assert {(s.station_id, a.amenity_id) for s, a, _ in near_pairs(store.stations.values(), store.amenities, 400)} == (
        brute_force
    )
    for c in _enriched(pipe_a, store, demo_text):
        ids = {e.evidence_id for e in c.evidence}
        for na in c.amenities:
            assert evidence_id(c.station.station_id, f"near[{na.amenity.amenity_id}].distance_m") in ids


# ------------------------------------------------------------------ request-scoped graph


def test_explicit_empty_scope_does_not_build_the_complete_store(store):
    """An empty route must stay small instead of interpreting empty as all records."""

    from rdflib import RDF

    from clickless.retrieval.kg import vocab as cc

    graph = build_graph(
        store,
        stations=[],
        amenities=[],
    )

    assert not list(graph.subjects(RDF.type, cc.Station))
    assert not list(graph.subjects(RDF.type, cc.Amenity))


def test_runtime_graph_contains_only_candidate_records(pipe_a, store, demo_text):
    """Runtime enrichment excludes unrelated national-snapshot records."""

    from rdflib import RDF

    from clickless.retrieval.kg import vocab as cc

    candidates, request = _demo_candidates(pipe_a, demo_text)
    selected_candidate = next(candidate for candidate in candidates if candidate.amenities)
    selected_amenity_ids = {nearby_amenity.amenity.amenity_id for nearby_amenity in selected_candidate.amenities}
    unrelated_station = next(
        station for station in store.stations.values() if station.station_id != selected_candidate.station.station_id
    )
    unrelated_amenity = next(amenity for amenity in store.amenities if amenity.amenity_id not in selected_amenity_ids)

    enricher = KGEnricher(store, {})
    enricher.enrich([selected_candidate], request)
    graph = enricher.graph

    assert (
        station_uri(selected_candidate.station.station_id),
        RDF.type,
        cc.Station,
    ) in graph
    assert (
        station_uri(unrelated_station.station_id),
        RDF.type,
        cc.Station,
    ) not in graph
    assert all((amenity_uri(amenity_id), RDF.type, cc.Amenity) in graph for amenity_id in selected_amenity_ids)
    assert (
        amenity_uri(unrelated_amenity.amenity_id),
        RDF.type,
        cc.Amenity,
    ) not in graph


def test_runtime_graph_is_reused_for_the_same_candidate_scope(
    pipe_a,
    store,
    demo_text,
):
    """Relaxation attempts on the same route reuse the already-built graph."""

    candidates, request = _demo_candidates(pipe_a, demo_text)
    selected_candidate = candidates[0]
    enricher = KGEnricher(store, {})

    enricher.enrich([selected_candidate], request)
    first_graph = enricher.graph

    fresh_candidates, fresh_request = _demo_candidates(pipe_a, demo_text)
    same_candidate = next(
        candidate
        for candidate in fresh_candidates
        if candidate.station.station_id == selected_candidate.station.station_id
    )
    enricher.enrich([same_candidate], fresh_request)

    assert enricher.graph is first_graph


def test_runtime_graph_is_replaced_for_a_different_candidate_scope(
    pipe_a,
    store,
    demo_text,
):
    """A later route cannot read station facts left by an earlier route."""

    from rdflib import RDF

    from clickless.retrieval.kg import vocab as cc

    candidates, request = _demo_candidates(pipe_a, demo_text)
    first_candidate = candidates[0]
    second_candidate = next(
        candidate for candidate in candidates if candidate.station.station_id != first_candidate.station.station_id
    )
    enricher = KGEnricher(store, {})

    enricher.enrich([first_candidate], request)
    first_graph = enricher.graph
    enricher.enrich([second_candidate], request)
    second_graph = enricher.graph

    assert second_graph is not first_graph
    assert (
        station_uri(first_candidate.station.station_id),
        RDF.type,
        cc.Station,
    ) not in second_graph
    assert (
        station_uri(second_candidate.station.station_id),
        RDF.type,
        cc.Station,
    ) in second_graph


# ------------------------------------------------------------------ graph and pipeline


def test_every_kg_term_is_declared_in_the_ontology():
    from rdflib import RDF, Graph

    from clickless.retrieval.kg import vocab

    ontology = Graph().parse(repo_path("src/clickless/retrieval/kg/ontology.ttl"), format="turtle")

    undeclared = [t for t in vocab.TERMS if (t, RDF.type, None) not in ontology]
    assert not undeclared


def test_graph_follows_the_competency_query_conventions(store):
    from rdflib.plugins.sparql import prepareQuery

    from clickless.retrieval.kg.build import station_uri

    cq3 = repo_path("src/clickless/retrieval/kg/competency_queries/cq03_amenity_wheelchair_status.rq").read_text()
    query = prepareQuery(cq3.replace("VALUES ?station { cc:station_REPLACE_ME }", ""))

    rows = build_graph(store).query(query, initBindings={"station": station_uri("FIX-ST-002")})

    assert [(str(r.amenity).rsplit("_", 1)[-1], str(r.wheelchair)) for r in rows] == [("fix%3Aa02", "unknown")]


def test_config_c_plans_on_the_fixtures_with_kg_evidence(store, demo_text):
    pipe_c = build_pipeline(load_config("C"), store)
    pipe_c.matcher = TfidfMatcher(store)  # Sentence-BERT needs the nlp extra, which CI does not install
    seen = []
    enrich = pipe_c.enricher.enrich
    pipe_c.enricher.enrich = lambda candidates, request: seen.append(enrich(candidates, request)) or seen[-1]

    result = pipe_c.run(demo_text, request_id="kg-c")

    assert result.status == "ok"
    stop = result.alternatives[0].itinerary.stops[0]
    stop_candidate = next(c for c in seen[0] if c.station.station_id == stop.station_id)
    cited = {e.evidence_id for e in stop_candidate.evidence}
    assert evidence_id(stop.station_id, "name") in cited
    assert {evidence_id(a, "category") for a in stop.amenity_ids} <= cited
