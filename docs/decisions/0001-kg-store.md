# ADR 0001: Knowledge-graph store and query language

- Status: proposed
- Owner: Manas
- Approvers: Aarya, Ian

## Context
The proposal names relations (SUPPORTS, HAS_PORT, NEAR, SUPPORTED_BY) but no store. Options:
RDF + rdflib + SPARQL (pure Python, Turtle files hash cleanly into the snapshot, PROV-O for
provenance, matches the Semantic Web course) vs. a property graph (Neo4j + Cypher: nicer
traversal ergonomics, but a server to run and less aligned with the course).

## Decision (proposed)
RDF with rdflib, ontology in `retrieval/kg/ontology.ttl`, provenance aligned with PROV-O,
competency questions as SPARQL `.rq` files. KG serialized as `data/snapshot/kg.ttl`.

## Consequences
No extra service for teammates or CI. If corridor-scale queries are too slow in rdflib, keep
the spatial index outside the KG (already the plan: KG enriches, it does not retrieve).
