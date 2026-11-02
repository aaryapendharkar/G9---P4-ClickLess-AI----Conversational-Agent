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
| runtime OSRM routing | strict HTTP client, deterministic local response cache, cache-first waypoint routing, and verifier measurement integration implemented; fallback/planner integration pending |
| energy model, charging-time estimate | v0 implemented |
| filters | v0 (adapters missing) |
| planner | v4 forward label-setting search with Pareto pruning, separate +15% consumption robustness, and comfort meal-stop top-ups to the pre-taper threshold |
| verifier | v3 independently replays SoC and validates station, connector, power, continuity, route-distance and detour claims; Config A uses geometry while OSRM mode uses cache-backed road measurements |
| weighted cost, trade-off selector | v0 implemented |
| logreg ranker | falls back to weighted cost until trained |
| template explainer, trade-off presenter | placeholder wording |
| Maps URL + approval gate | v0 (no segmentation) |
| harness | runs; most metrics stubbed |
| minimum-cost relaxation | integrated: penalty-ordered requests reuse the normal retrieve, filter, plan, verify, rank, and select path; changes require driver consent |
| LLM parser, SBERT, KG build/enrich, grounded explainer, AFDC/OSM/vehicle ingestion, oracle, splits, statistics | stubs (`NotImplementedError`) |

Planner label/frontier primitives live in `planning/label_search.py`. Planning
tests are grouped by energy and charging, filtering, search, and verification
responsibility so each behavior has a focused place to evolve.

Planner implementation responsibilities are also separated: `planner.py`
orchestrates nominal/robust runs and forward stop search,
`itinerary_simulation.py` constructs complete itineraries, and
`route_points.py` owns the shared distance, drive-time, and departure-charge
formulas used by both search and simulation.

Robust feasibility is evaluated through `ItineraryPlanner.plan_robust`. It
reuses the normal stop search and simulation with the frozen
`efficiency_penalty_robust` value. This sensitivity run may remove marginal
plans or require more stops, but ordinary pipeline planning continues to use
the nominal energy model.

Departure-charge targets come from `planning/charging_policy.py`. Balanced and
fastest profiles retain the just-enough target. At a stop that satisfies a
requested food preference, the comfort profile may charge to the existing
pre-taper threshold (80% in the committed energy model), capped by the maximum
departure SoC. Search and final simulation call the same policy.

Configuration A's verifier measurements remain deterministic geometric estimates,
not live road reroutes. All committed A/B/C configurations still select fixture
routing, so tests and demos remain offline and repeatable.

When routing mode is switched to OSRM, the pipeline injects a cache-backed
measurement strategy into the verifier. It independently requests each
consecutive itinerary leg and each single-station detour through the waypoint
router, rather than accepting distances copied from the planner.

OSRM mode now uses a cache-first wrapper. A request is identified by its ordered
coordinates and routing options. A cache hit returns a validated route labelled
`cached`; a miss calls the strict OSRM client, stores the validated runtime route,
and returns it. Cache files are generated under `data/cache/` and are excluded
from version control.

OSRM failures are now classified before routing policy handles them. Timeouts,
connection failures, and HTTP 5xx responses are temporary and eligible for
geometric fallback. `NoRoute`, invalid input, non-retryable HTTP responses,
malformed responses, and configuration errors are not fallback-eligible.

Remaining routing milestones:

- add an explicit routing-policy layer that invokes geometric fallback only for
  errors classified as temporary;
- preserve the selected measurement source and a user-visible warning when
  fallback is used;
- connect selected OSRM measurements to planning; and
- add tests for fallback provenance, warnings, and verifier agreement.
