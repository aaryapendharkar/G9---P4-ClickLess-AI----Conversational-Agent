"""Final KG namespace and PROV-O alignment (issue #159, ADR 0019)."""

import re

import pytest

from clickless.config import repo_path
from clickless.retrieval.kg.build import CC_NS, DEFAULT_ONTOLOGY, add_explanation, build_graph, explanation_uri
from clickless.retrieval.kg.competency import CQ_DIR, run_competency_query

pytest.importorskip("rdflib")  # CI installs the kg extra; this only skips on a machine without it

from rdflib import OWL, RDF, RDFS, Graph, Literal, Namespace, URIRef  # noqa: E402

PROV = Namespace("http://www.w3.org/ns/prov#")
CC = Namespace(CC_NS)
PLACEHOLDER_NS = "https://example.org/clickless#"

# The whole alignment (ADR 0019 §2). Anything added or removed must be decided there first.
PROV_AXIOMS = {
    (CC.Source, RDFS.subClassOf, PROV.Entity),
    (CC.Claim, RDFS.subClassOf, PROV.Entity),
    (CC.Explanation, RDFS.subClassOf, PROV.Collection),
    (CC.supportedBy, RDFS.subPropertyOf, PROV.wasDerivedFrom),
    (CC.hasClaim, RDFS.subPropertyOf, PROV.hadMember),
    (CC.retrievedAt, RDFS.subPropertyOf, PROV.generatedAtTime),
}
MAPPING_PREDICATES = {RDFS.subClassOf, RDFS.subPropertyOf, OWL.equivalentClass, OWL.equivalentProperty}


@pytest.fixture(scope="module")
def ontology():
    return Graph().parse(repo_path(DEFAULT_ONTOLOGY), format="turtle")


# ------------------------------------------------------------------ namespace


def _code_lines(path):
    """The file without its comment lines (the ontology's version history names the old namespace)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


def test_no_kg_file_uses_the_placeholder_namespace():
    kg_dir = repo_path("src/clickless/retrieval/kg")
    files = [p for p in kg_dir.rglob("*") if p.suffix in {".ttl", ".rq", ".py", ".json"}]
    assert any(p.suffix == ".rq" for p in files) and any(p.suffix == ".ttl" for p in files)
    offenders = [p.name for p in files if "example.org" in _code_lines(p)]
    assert not offenders


def test_ontology_declares_the_final_namespace(ontology):
    assert CC_NS == "https://w3id.org/clickless#"
    assert dict(ontology.namespaces())["cc"] == URIRef(CC_NS)
    iri = URIRef(CC_NS.rstrip("#"))
    assert (iri, RDF.type, OWL.Ontology) in ontology
    assert ontology.value(iri, OWL.versionInfo) == Literal("0.4")
    assert not [t for t in ontology if any(str(x).startswith(PLACEHOLDER_NS) for x in t)]


def test_every_competency_query_declares_the_namespace():
    for path in sorted(CQ_DIR.glob("cq*.rq")):
        prefixes = re.findall(r"^PREFIX\s+cc:\s+<([^>]+)>", path.read_text(encoding="utf-8"), re.M)
        assert prefixes == [CC_NS], path.name


def test_docs_use_the_final_namespace():
    for doc in ("docs/ARCHITECTURE.md", "docs/COMPETENCY_QUESTIONS.md"):
        text = repo_path(doc).read_text(encoding="utf-8")
        assert CC_NS in text and PLACEHOLDER_NS not in text, doc


# ------------------------------------------------------------------ PROV-O mappings


def test_prov_mappings_are_exactly_the_documented_ones(ontology):
    mappings = {(s, p, o) for s, p, o in ontology if p in MAPPING_PREDICATES and str(o).startswith(str(PROV))}
    assert mappings == PROV_AXIOMS
    # one-way only: no ClickLess term is declared equivalent to anything
    assert not list(ontology.triples((None, OWL.equivalentClass, None)))
    assert not list(ontology.triples((None, OWL.equivalentProperty, None)))


def test_prov_terms_used_are_declared_with_their_prov_o_kind(ontology):
    kinds = {PROV.Entity: OWL.Class, PROV.Collection: OWL.Class, PROV.wasDerivedFrom: OWL.ObjectProperty,
             PROV.hadMember: OWL.ObjectProperty, PROV.generatedAtTime: OWL.DatatypeProperty}
    assert {o for _, _, o in PROV_AXIOMS} == set(kinds)
    for term, kind in kinds.items():
        assert (term, RDF.type, kind) in ontology, term


def test_mapped_cc_terms_keep_compatible_domains(ontology):
    """PROV-O's domains: wasDerivedFrom and generatedAtTime on entities, hadMember on collections."""
    assert ontology.value(CC.supportedBy, RDFS.domain) == CC.Claim
    assert ontology.value(CC.supportedBy, RDFS.range) == CC.Source
    assert ontology.value(CC.retrievedAt, RDFS.domain) == CC.Source
    assert ontology.value(CC.hasClaim, RDFS.domain) == CC.Explanation
    assert ontology.value(CC.hasClaim, RDFS.range) == CC.Claim


# ------------------------------------------------------------------ the PROV view of the KG

PROV_VIEW = """
PREFIX prov: <http://www.w3.org/ns/prov#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
SELECT ?claim ?source ?generated
WHERE {
  ?explanation a ?collection . ?collection rdfs:subClassOf* prov:Collection .
  ?explanation ?member ?claim .  ?member rdfs:subPropertyOf* prov:hadMember .
  ?claim a ?claimClass .         ?claimClass rdfs:subClassOf* prov:Entity .
  ?claim ?derived ?source .      ?derived rdfs:subPropertyOf* prov:wasDerivedFrom .
  ?source a ?sourceClass .       ?sourceClass rdfs:subClassOf* prov:Entity .
  ?source ?time ?generated .     ?time rdfs:subPropertyOf* prov:generatedAtTime .
}
ORDER BY ?claim ?source
"""


@pytest.fixture(scope="module")
def explained(store):
    """The fixture KG plus a real Config C explanation, as CQ9 reads it."""
    from clickless.config import load_config
    from clickless.pipeline import build_pipeline
    from clickless.retrieval.tfidf_matcher import TfidfMatcher

    pipe = build_pipeline(load_config("C"), store)
    pipe.matcher = TfidfMatcher(store)  # Sentence-BERT needs the nlp extra, which CI does not install
    result = pipe.run(
        "From Phoenix to Flagstaff in my Spark LR, I have 65% battery and want to arrive with at "
        "least 20%, lunch stop and a wheelchair accessible restroom please",
        request_id="prov-view",
    )
    assert result.status == "ok" and result.evidence
    g = build_graph(store)
    add_explanation(g, result)
    return g


def test_prov_only_query_returns_the_cq9_claim_sources(explained):
    """A reader who knows only PROV-O finds the same claim -> source -> time links as CQ9."""
    cq9 = run_competency_query(explained, "cq09", explanation=explanation_uri("prov-view"))
    expected = [(r.claim, r.source, r.retrievedAt) for r in cq9 if r.source is not None]
    got = [(r.claim, r.source, r.generated) for r in explained.query(
        PROV_VIEW, initBindings={"explanation": explanation_uri("prov-view")})]
    assert expected and got == expected


def test_the_stored_graph_has_no_materialized_prov_triples(explained):
    """The alignment is TBox only: data keep cc: terms, as for cc:supportsViaAdapter (CQ5)."""
    assert not [t for t in explained if str(t[1]).startswith(str(PROV))]  # no prov: predicate
    assert not [s for s, o in explained.subject_objects(RDF.type) if str(o).startswith(str(PROV))]  # no prov: type
