# Architecture

## Online pipeline (proposal Fig. 1) → code

```
text ─► RequestParser ─► ParseResult ──missing?──► reject (A,B) / Clarifier (C)
          nlu/                │ TripRequest
                              ▼
                  Router.route (planning/routing) ─► Route
                              ▼
         CandidateRetriever (retrieval/corridor) ─► [Candidate]  (+ nearby amenities ≤ 400 m)
                              ▼
         PreferenceMatcher.score (tfidf | sbert)  ─► Candidate.amenities[].match_scores
         Router.detour_min                        ─► Candidate.detour_min_est
         KGEnricher (C only)                      ─► Candidate.evidence
                              ▼
         HardConstraintFilter (planning/filters)  ─► kept + rejected(reason)
                              ▼
         Planner (planning/planner)               ─► [Itinerary]
                              ▼
         Verifier (planning/verifier)  — rechecks every itinerary from scratch
                              ▼            (none feasible → MinCostRelaxer)
         Ranker (ranking/*_ranker)                ─► [(Itinerary, PlanCost)]
                              ▼
         TradeoffSelector (ranking/tradeoffs)     ─► [Alternative] (Recommended + Pareto alts)
                              ▼
         Explainer (explain/templates | grounded) ─► text + claims
                              ▼
   user approves ─► Pipeline.approve ─► GoogleMapsHandoff ─► URL(s)  (+ audit log)
```

Orchestration: `src/clickless/pipeline.py`. Configs choose implementations:

| config key | A | B | C |
|---|---|---|---|
| parser | rules | llm | llm |
| matcher | tfidf | sbert | sbert |
| retriever | corridor | corridor | corridor+kg |
| ranker | logreg | weighted | weighted+evidence |
| incomplete_request | reject | reject | clarify |
| explainer | template | template | grounded_llm |
| selector (trade-offs) | tradeoff | tradeoff | tradeoff |

Planner, filters, verifier and the approval gate are identical in all configs (proposal Table II).

## Offline pipeline

`data/afdc.py`, `data/vehicles.py`, `data/osm_amenities.py` → normalized JSON in the fixture
layout → `data/snapshot/` + `MANIFEST.json` (hashes) → `retrieval/kg/build.py` → `kg.ttl`.

## Dependency rules (enforced by tests/review)

1. Modules talk only through `clickless.contracts` types and the Protocols in
   `contracts/interfaces.py`. `contracts/` imports nothing else from the project.
2. `evaluation.oracle` must not import `clickless.planning`, `clickless.ranking` or the pipeline.
3. Only `pipeline.py` knows which implementation is used; modules never import siblings'
   internals (e.g. `explain/` reads `PlanCost`, it does not call `ranking.cost`).
4. Numbers shown to the user come from contract fields, never recomputed in explanation code.
5. All randomness seeded; LLM temperature 0; prompts versioned; data frozen by hash.

## Implementation status (walking skeleton, Oct 30)

| Component | Status |
|---|---|
| contracts, configs, fixtures, DataStore, geo utils | done (contracts v0.3) |
| rules parser, template clarifier, priority phrases | placeholder (fixture phrasings) |
| corridor retrieval | placeholder (brute force) |
| TF-IDF matcher | placeholder (category equality) |
| cached router + detour | placeholder (straight-line detour) |
| energy model, charging-time estimate | v0 implemented |
| filters | v0 (adapters missing) |
| planner | v1 generalized forward label-setting search (direct + multi-stop); richer time/SoC dominance remains TODO |
| verifier | v2 independently replays SoC and validates station, connector, power, continuity, route-distance and detour claims; Config A measurements use cached geometry + authoritative station coordinates |
| weighted cost, trade-off selector | v0 implemented |
| logreg ranker | falls back to weighted cost until trained |
| template explainer, trade-off presenter | placeholder wording |
| Maps URL + approval gate | v0 (no segmentation) |
| harness | runs; most metrics stubbed |
| LLM parser, SBERT, KG build/enrich, grounded explainer, OSRM, AFDC/OSM/vehicle ingestion, relaxation, oracle, splits, statistics | stubs (`NotImplementedError`) |

Configuration A's verifier measurements are deterministic estimates, not live road reroutes.
Cached or runtime OSRM waypoint measurements remain future routing work.
