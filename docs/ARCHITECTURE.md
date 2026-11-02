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

## Implementation status (walking skeleton, Nov 1)

| Component | Status |
|---|---|
| contracts, configs, fixtures, DataStore, geo utils | done (contracts v0.3) |
| rules parser, template clarifier, priority phrases | placeholder (fixture phrasings) |
| corridor retrieval | placeholder (brute force) |
| TF-IDF matcher | placeholder (category equality) |
| cached router + detour | placeholder (straight-line detour) |
| runtime OSRM routing | strict client, local cache, waypoint routing, verifier measurement, and temporary-failure fallback with user-visible estimate warnings implemented |
| energy model, charging-time estimate | v0 implemented |
| filters | v0 (adapters missing) |
| planner | v4 forward label-setting search with Pareto pruning, separate +15% consumption robustness, and comfort meal-stop top-ups to the pre-taper threshold |
| verifier | v3 independently replays SoC and validates station, connector, power, continuity, route-distance and detour claims; Config A uses geometry while OSRM mode uses cache-backed road measurements |
| weighted cost, trade-off selector | v0 implemented |
| logreg ranker | standardized training, validation, save/load, probability ranking, and missing-model fallback implemented; final model awaits labeled DEV plans |
| template explainer, trade-off presenter | template explanation v0; natural, contract-grounded alternative comparisons implemented |
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

Configuration A's `LogRegRanker` uses the five inputs listed by `FEATURE_NAMES`
in `ranking/logreg_ranker.py`. `extract_feature_values` produces values in that
same order. Training standardizes those features and fits the classifier as one
scikit-learn pipeline, then saves that complete pipeline with joblib so runtime
prediction applies the same preprocessing used during training.

The default artifact path is `results/models/logreg_a.joblib`. Generated results
are intentionally excluded from version control. When that artifact is absent,
the ranker explicitly delegates to `WeightedRanker`, and the pipeline reports the
fallback in its notes. The synthetic ranker tests verify this lifecycle; they do
not replace the real labeled DEV candidate plans needed to train the final model.
Held-out TEST labels must not be used for training or tuning.

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

`TemporaryFailureFallbackMeasurer` applies that rule without weakening the
strict OSRM client. Successful measurements remain labelled `osrm`. Only an
`OSRMTemporaryError` delegates to the deterministic geometric measurer, records
`geometry_fallback` as the selected source, and creates an estimate warning.
The source and warning are reset for every attempt so a recovered service does
not retain stale fallback state. In OSRM mode, the shared pipeline now wraps the
cache-first verifier measurer with this policy and appends its warning exactly
once after final verification of the recommended plan. Cache-mode configurations
continue to use deterministic geometry directly and do not report an OSRM warning.

Remaining routing milestones:

- add a separate temporary-failure policy for candidate-detour measurements
  used during planning;
- connect routed leg measurements to itinerary construction; and
- add integration tests for planner/verifier measurement agreement.
