# Competency questions (owner: Manas; CQ2–CQ6 are Aarya's learning swap)

Ontology adequacy = share of these 20 questions answerable by a SPARQL query over the KG
(proposal §V-C, Grüninger & Fox). Each CQ gets a `.rq` file in
`src/clickless/retrieval/kg/competency_queries/` and an expected-answer test on the fixtures.

| # | Question | Query file | Status |
|---|---|---|---|
| 1 | Which stations have a port vehicle V can use, and at what power? | cq01_compatible_stations.rq | draft |
| 2 | Which compatible stations have a food amenity within 400 m? | cq02_compatible_stations_with_food.rq | draft (Aarya) |
| 3 | Which amenities near station S are tagged wheelchair-accessible (and which are unknown)? | cq03_amenity_wheelchair_status.rq | draft (Aarya) |
| 4 | What is the source and retrieval time of the power value of station S's DC port? | cq04_dc_power_provenance.rq | draft (Aarya) |
| 5 | Which connectors does vehicle V support only through an adapter? | cq05_adapter_only_connectors.rq | draft (Aarya) |
| 6 | Which stations' records were updated at the source more than N days before retrieval? | cq06_stale_station_records.rq | draft (Aarya) |
| 7 | Which network operates station S? | | Manas |
| 8 | Which stations offer ≥ P kW for connector C? | | Manas |
| 9 | Which claims in explanation E are supported by which sources? | | Manas |
| 10–20 | TODO (Manas): cover access restrictions, status, opening hours, port counts, vehicle max DC power, evidence freshness labels | | |

## Ontology terms needed by CQ2–CQ6 (proposal for Manas)
CQ2–CQ6 are drafts: they cannot return rows until `retrieval/kg/build.py` builds the graph.
`tests/retrieval/test_competency_queries.py` checks their structure everywhere and parses them
with rdflib where it is installed (`pip install -e ".[kg]"`). They use these terms, which
`ontology.ttl` does not define yet:

| Term | Domain → range | Used by | Value |
|---|---|---|---|
| `cc:amenityCategory` | Amenity → xsd:string | CQ2 | `Amenity.category` (`food`, `coffee`, `restroom`, ...) |
| `cc:chargerLevel` | ChargingPort → xsd:string | CQ4 | `ChargingPort.level` (`dc_fast`, `l2`, `l1`) |
| `cc:hasProvenance` | Station → Source | CQ4, CQ6 | one Source node per station record |
| `cc:sourceName` | Source → xsd:string | CQ4 | `Provenance.source` (`AFDC`, `OSM`, ...) |

Graph-building assumptions: `cc:near` edges only for amenities within `amenity_radius_m`
(400 m); a missing wheelchair tag means no `cc:wheelchair` triple; native connectors use
`cc:supports` and adapter connectors only `cc:supportsViaAdapter` (no RDFS materialization in
the stored graph, otherwise CQ5 returns nothing); a port with unknown power has no `cc:powerKw`.
