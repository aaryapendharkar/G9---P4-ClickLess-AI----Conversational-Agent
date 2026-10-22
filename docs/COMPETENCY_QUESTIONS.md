# Competency questions (owner: Manas; CQ2–CQ6 are Aarya's learning swap)

Ontology adequacy = share of these 20 questions answerable by a SPARQL query over the KG
(proposal §V-C, Grüninger & Fox). Each CQ gets a `.rq` file in
`src/clickless/retrieval/kg/competency_queries/` and an expected-answer test on the fixtures.

| # | Question | Query file | Status |
|---|---|---|---|
| 1 | Which stations have a port vehicle V can use, and at what power? | cq01_compatible_stations.rq | draft |
| 2 | Which compatible stations have a food amenity within 400 m? | | Aarya |
| 3 | Which amenities near station S are tagged wheelchair-accessible (and which are unknown)? | | Aarya |
| 4 | What is the source and retrieval time of the power value of station S's DC port? | | Aarya |
| 5 | Which connectors does vehicle V support only through an adapter? | | Aarya |
| 6 | Which stations' records were updated at the source more than N days before retrieval? | | Aarya |
| 7 | Which network operates station S? | | Manas |
| 8 | Which stations offer ≥ P kW for connector C? | | Manas |
| 9 | Which claims in explanation E are supported by which sources? | | Manas |
| 10–20 | TODO (Manas): cover access restrictions, status, opening hours, port counts, vehicle max DC power, evidence freshness labels | | |
