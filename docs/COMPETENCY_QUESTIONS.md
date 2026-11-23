# Competency questions (owner: Manas; CQ2–CQ6 are Aarya's learning swap)

Ontology adequacy = share of these 20 questions answerable by a SPARQL query over the KG
(proposal §V-C, Grüninger & Fox). Each CQ gets a `.rq` file in
`src/clickless/retrieval/kg/competency_queries/` and an expected-answer test on the fixtures.

| # | Question | Query file | Status |
|---|---|---|---|
| 1 | Which stations have a port vehicle V can use, and at what power? | cq01_compatible_stations.rq | tested on the fixture KG (Aarya for Manas, #157; `test_competency_query_answers.py`) |
| 2 | Which compatible stations have a food amenity within 400 m? | cq02_compatible_stations_with_food.rq | tested on the fixture KG (Aarya; `test_competency_query_answers.py`) |
| 3 | Which amenities near station S are tagged wheelchair-accessible (and which are unknown)? | cq03_amenity_wheelchair_status.rq | tested on the fixture KG (Aarya; `test_competency_query_answers.py`) |
| 4 | What is the source and retrieval time of the power value of station S's DC port? | cq04_dc_power_provenance.rq | tested on the fixture KG (Aarya; `test_competency_query_answers.py`) |
| 5 | Which connectors does vehicle V support only through an adapter? | cq05_adapter_only_connectors.rq | tested on the fixture KG (Aarya; `test_competency_query_answers.py`) |
| 6 | Which stations' records were updated at the source more than N days before retrieval? | cq06_stale_station_records.rq | tested on the fixture KG (Aarya; `test_competency_query_answers.py`) |
| 7 | Which network operates station S? | cq07_station_network.rq | tested on the fixture KG (Aarya for Manas, #157) |
| 8 | Which stations offer ≥ P kW for connector C? | cq08_stations_min_power.rq | tested on the fixture KG (Aarya for Manas, #157) |
| 9 | Which claims in explanation E are supported by which sources? | cq09_claim_sources.rq | tested on the fixture KG and a real Config C result (Aarya for Manas, #157) |
| 10–20 | TODO (Manas): cover access restrictions, status, opening hours, port counts, vehicle max DC power, evidence freshness labels | | |

## Ontology terms and conventions used by CQ1–CQ9
CQ2–CQ6 have expected-answer tests on the fixture KG (`retrieval/kg/build.py::build_graph`) in
`tests/retrieval/test_competency_query_answers.py`. Cases the fixtures lack (a connector both
native and adapted, an unknown-power DC port, dated records, "limited"/"no" wheelchair tags) are
made by copying fixture records and rebuilding the graph. `tests/retrieval/test_competency_queries.py`
checks every query's structure and parses it with rdflib where it is installed (`pip install -e ".[kg]"`).

**Running a CQ:** `clickless.retrieval.kg.competency.run_competency_query(graph, "cq04", station=...)`.
It removes each `VALUES ?x { cc:x_REPLACE_ME }` placeholder and binds `?x` instead, and refuses
to run with a placeholder left (which would match nothing and return `[]`). A float binding (CQ8's
`minKw=7.2`) becomes an exact decimal, as the graph stores numbers, so "at least 7.2 kW" matches a
7.2 kW port. Every query has an `ORDER BY` that fixes the row order completely (tested by loading
the graph's triples in reverse). CQ6 takes
`cutoff=clickless.data.provenance.stale_cutoff(retrieved_at)`: a record is stale exactly when its
source update time is at or before the cutoff, the same rule as `provenance.is_stale`.

They use these terms, defined in `ontology.ttl` (v0.2, ADR 0015; v0.3 adds the CQ9 terms):

| Term | Domain → range | Used by | Value |
|---|---|---|---|
| `cc:amenityCategory` | Amenity → xsd:string | CQ2 | `Amenity.category` (`food`, `coffee`, `restroom`, ...) |
| `cc:chargerLevel` | ChargingPort → xsd:string | CQ4 | `ChargingPort.level` (`dc_fast`, `l2`, `l1`) |
| `cc:hasProvenance` | Station → Source | CQ4, CQ6 | one Source node per station record |
| `cc:sourceName` | Source → xsd:string | CQ4 | `Provenance.source` (`AFDC`, `OSM`, ...) |
| `cc:dataStatus` | Source → xsd:string | CQ6 | `Provenance.status`; static reference data is never judged stale |
| `cc:Explanation`, `cc:hasClaim` (v0.3) | Explanation → Claim | CQ9 | written by `build.add_explanation(graph, result)` |
| `cc:Claim` + `cc:supportedBy` | Claim → Source | CQ9 | the source of each `kg:` fact the claim cites that is in `result.evidence` (ADR 0015) |

Graph-building assumptions: `cc:near` edges only for amenities within `amenity_radius_m`
(400 m); a missing wheelchair tag means no `cc:wheelchair` triple; native connectors use
`cc:supports` and adapter connectors only `cc:supportsViaAdapter` (no RDFS materialization in
the stored graph, otherwise CQ5 returns nothing); a documented adapter counts at DC fast ports
only, as in `planning/filters.py::compatible_ports` (CQ1, CQ2); a port with unknown power has no `cc:powerKw`.
