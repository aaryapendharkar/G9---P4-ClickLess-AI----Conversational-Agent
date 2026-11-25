# ADR 0019: Final KG namespace and PROV-O alignment (issue #159)

- Status: proposed
- Date: 2026-11-25
- Owner: Manas (retrieval / KG)
- Approvers: Deepti (grounded explanations), Ian (evaluation provenance), Aarya (source-data
  provenance). Two approvals are needed (`docs/TEAM_PLAN.md`), and #159 asks all three to review.
- Related: #159 (this decision), ADR 0001 (rdflib + SPARQL, "provenance aligned with PROV-O"),
  ADR 0015 (KG evidence and its provenance), #157/#158 (CQ1–CQ20), PR #168 (ontology adequacy)

## Context
`retrieval/kg/ontology.ttl` v0.3 and all 20 competency queries used the placeholder namespace
`https://example.org/clickless#`. The ontology carried a TODO to replace it and to align
Claim/Source with PROV-O, which ADR 0001 promised. Only `cc:Source rdfs:subClassOf prov:Entity`
existed. The IRIs are shared: Aarya's CQ files declare the prefix, Ian's ontology-adequacy metric
(#158) runs those files, Deepti's claims are written into the graph for CQ9, and anyone who reads
a serialized graph sees the IRIs.

## Decision

### 1. Namespace
**`https://w3id.org/clickless#`**, prefix `cc:`. The ontology IRI is `https://w3id.org/clickless`
(`owl:Ontology`, `owl:versionInfo "0.4"`).

- The W3C Permanent Identifier Community Group runs w3id.org as a redirect service for
  vocabularies, so the IRI does not depend on a GitHub account, a repository name or hosting,
  any of which can change after the course. A redirect to `ontology.ttl` can be registered later
  with a pull request to `perma-id/w3id.org`, and no IRI has to change. Until then the IRI is an
  identifier that does not resolve. RDF does not require it to resolve.
- It stays a hash namespace, and terms and data IRIs share it as before (`cc:station_<id>`,
  `cc:source_station_<id>`, `cc:explanation_<request_id>_claim_0000`, ...). Local names are
  unchanged.
- `retrieval/kg/build.py::CC_NS` is the only definition in Python. `ontology.ttl` and every `.rq`
  file declare the same `cc:` prefix, and tests check that all three agree.

### 2. PROV-O alignment
| ClickLess term | Axiom | Why the meaning matches |
|---|---|---|
| `cc:Source` | `rdfs:subClassOf prov:Entity` | one source record as ClickLess retrieved it (AFDC station, OSM amenity, curated vehicle row, or a DERIVED value such as a haversine distance) |
| `cc:retrievedAt` | `rdfs:subPropertyOf prov:generatedAtTime` | our copy of the record exists from its retrieval on. For a DERIVED source it is the later input's retrieval time (ADR 0015 §4), the earliest time the computed value can exist |
| `cc:Claim` | `rdfs:subClassOf prov:Entity` | a sentence the explainer generated |
| `cc:supportedBy` | `rdfs:subPropertyOf prov:wasDerivedFrom` | a claim gets this link only when it cites a fact from that record and the result carries the fact (ADR 0015 §5), so the claim was made from that record |
| `cc:Explanation` | `rdfs:subClassOf prov:Collection` | a structured set of claims |
| `cc:hasClaim` | `rdfs:subPropertyOf prov:hadMember` | links the explanation to each of its claims |

- **One-way only.** Each ClickLess term is narrower than its PROV term (every claim is a
  `prov:Entity`, but not every entity is a claim). So the ontology uses no `owl:equivalentClass`
  or `owl:equivalentProperty`.
- **Axioms only (TBox).** `build.py` keeps writing `cc:` triples only, and the stored graph has no
  `prov:` triples, the same convention CQ5 relies on for `cc:supportsViaAdapter`. A PROV-O
  consumer gets the `prov:` view from these axioms with an RDFS reasoner, or with SPARQL property
  paths (`?claim ?p ?source . ?p rdfs:subPropertyOf* prov:wasDerivedFrom`).
- `ontology.ttl` declares the five PROV-O terms it uses (`prov:Entity`, `prov:Collection`,
  `prov:wasDerivedFrom`, `prov:hadMember`, `prov:generatedAtTime`) with the same kinds PROV-O gives
  them. It has no `owl:imports`: rdflib never fetches imports, and OWL tools then work offline.
- `cc:retrievedAt` and `cc:sourceUpdatedAt` now have `rdfs:domain cc:Source`, the only place
  `build.py` writes them.

**Deliberately not mapped:**

| ClickLess term | Why not |
|---|---|
| `cc:hasProvenance` | Its subjects are stations, amenities and station–amenity proximities, i.e. the things, not records about them. `prov:wasDerivedFrom` or `prov:hadPrimarySource` would state that a charging station was derived from a database record, and would make it a `prov:Entity`. |
| `cc:sourceUpdatedAt` | It is the generation time of the upstream revision, which is a different entity from our retrieved copy. A second `prov:generatedAtTime` would give one entity two generations. |
| `cc:sourceName` | It is a literal (`AFDC`, `OSM`, ...). `prov:wasAttributedTo` needs a `prov:Agent` node. |
| `cc:sourceRecordId`, `cc:sourceUrl` | These identify the upstream record. PROV-O has no property for them, and `prov:atLocation` takes a `prov:Location`, not a URL literal. |
| `cc:dataStatus` | ClickLess's `static` / `cached` / `runtime` label has no PROV counterpart. |

### 3. Tests
- `tests/retrieval/test_kg_prov_alignment.py` checks:
  - the PROV-O axioms are exactly the six above, with no equivalences;
  - the PROV terms are declared with their PROV-O kinds, and the mapped terms keep compatible
    domains;
  - a query that uses only PROV-O terms returns exactly CQ9's claim → source → time rows for a
    real Config C explanation;
  - the stored graph has no `prov:` triples;
  - no KG file, and neither the architecture doc nor the CQ doc, uses the placeholder namespace.
- `tests/retrieval/test_competency_queries.py` checks that each query declares
  `PREFIX cc: <CC_NS>`.

## Migration consequences
- **Evidence ids: unchanged.** `kg:<subject_id>:<field>` (ADR 0015 §3) contains no IRI. Saved
  `PlanResult`s, `claim_support` and the evaluation results are unaffected, and nothing has to be
  re-run.
- **Contracts: unchanged.** `CONTRACTS_VERSION` stays the same.
- **Serialized RDF: none to migrate.** The repository and the snapshot hold no graph. The KG is
  built in memory per process (`kg.ttl` is still a TODO in `build.py`). Anyone who has a Turtle
  dump or query text from before this change replaces the namespace string. Local names are the
  same.
- **Competency queries.** Only the `PREFIX cc:` line of each query changed. On the fixture KG, all
  20 CQs return the same rows before and after: 111 runs and 177 rows, comparing IRIs by local
  name. The runs used each query's inventory example bindings, the station- or vehicle-bound
  queries once more for every fixture station and vehicle, and CQ9 on a real Config C result. The
  fixture graph grows from 466 to 482 triples: the ontology header plus the alignment. No data
  triple changes.
- Inventory example bindings (`"cc:station_FIX-ST-004"`) resolve through `CC_NS`, and PR #168's
  metric runs the queries through `run_competency_query`. Neither needs a change.

## Alternatives considered
- **A GitHub Pages or raw GitHub URL.** It resolves today, but it ties every IRI to an account and
  a repository name.
- **`urn:clickless:`.** It is stable but can never resolve, and it is unusual for an OWL
  ontology.
- **A separate data namespace** (`https://w3id.org/clickless/kg/`). This splits schema IRIs from
  data IRIs more cleanly, but it touches every CQ placeholder, the inventory examples and the
  placeholder convention, and no consumer needs it yet. Only `build.py` mints data IRIs, so a
  later ADR can still make this change.
- **Materializing `prov:` triples in the graph.** It doubles the provenance triples and breaks
  the convention that the stored graph is not materialized.
- **Qualified PROV** (retrieval as a `prov:Activity`, AFDC/OSM as `prov:Agent`s). It is more
  complete, but it adds nodes for every record and no CQ needs them. It stays compatible with
  these axioms if it is added later.

## Consequences
- The ontology has a stable namespace and an explicit PROV-O reading. Generic PROV tools can trace
  a claim to the record it was made from, and to that record's retrieval time.
- Each future `cc:` → `prov:` mapping has to be added here and in the test's axiom set.
- Follow-up (optional): register the w3id.org redirect to the published `ontology.ttl`.

## Approvals
Each approver records approval here (name, date, PR comment or sync) before the status changes to
accepted.

| Approver | Decision they approve | Review | Status |
|---|---|---|---|
| Deepti (grounded explanations) | §2: claims are `prov:Entity`s, `cc:supportedBy` ⊑ `prov:wasDerivedFrom`, explanations are `prov:Collection`s | requested (#159) | pending |
| Ian (evaluation provenance) | §1 namespace, and the migration section: evidence ids and evaluation results unchanged, CQs answer the same | requested (#159) | pending |
| Aarya (source-data provenance, CQ files) | §2 `cc:Source` / `cc:retrievedAt` mapping and the unmapped source-record terms; the `PREFIX` change in her CQ files | requested (#159) | pending |
