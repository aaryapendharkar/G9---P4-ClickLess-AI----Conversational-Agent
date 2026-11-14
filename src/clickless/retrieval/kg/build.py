"""Load a DataStore into an RDF graph (offline pipeline). Owner: Manas.

Plan: ontology.ttl + one node per Station/Port/Vehicle/Amenity + NEAR edges (<= 400 m, with
distance) + a Source node per provenance record. Serialize to data/snapshot/kg.ttl and record
its hash in the snapshot manifest.
"""

from __future__ import annotations


def build_graph(store, ontology_path: str = "src/clickless/retrieval/kg/ontology.ttl"):
    raise NotImplementedError("TODO(Manas): DataStore -> rdflib.Graph")
