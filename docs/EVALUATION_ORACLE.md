# Independent feasibility oracle

Owner: Ian. Reviewer: Manas (not Hriday, for independence). Code: `src/evaluation/oracle/`
(`energy_reference.py`, `label_setting.py`, `_geometry.py`), metrics in `src/evaluation/metrics/planning.py`.

## Why it exists
The planner (`clickless.planning`) proposes itineraries and its verifier checks them. If both share a
misreading of the documented model, the verifier cannot catch it. The oracle is a **second
implementation written only from the documentation**. It answers three questions for every
benchmark item: is the trip feasible at all, which plans are Pareto-optimal, and does a returned
itinerary break any hard constraint.

## Independence rule
- The oracle may reuse only: data loading (`DataStore`, snapshot/fixture loaders), route access, contracts and configs.
- It may not import (or be written by reading) `clickless.planning`, `clickless.ranking`, `clickless.retrieval` or
  `clickless.pipeline`. The same applies to `evaluation/metrics/planning.py`.
- Enforced by `tests/evaluation/test_oracle_independence.py`. One check reads the source (every import form, including relative ones). The other
  imports the oracle in a fresh interpreter and inspects `sys.modules`, which catches transitive imports. `tests/test_architecture.py` also checks it.
- `clickless.common.geo` is not reused either (retrieval likely uses it). The oracle has its own `_geometry.py`.
- Sources the rules come from: the oracle docstrings, `docs/TRADEOFFS.md` §3, `configs/energy.yaml`, ADRs 0009, 0010, 0012.

## Inputs: where stations, routes and distances come from
| Input | Source |
|---|---|
| stations, vehicles, places | `DataStore` (`stations`, `vehicles`, `geocode`) |
| request | the item's **gold** request (`request_from_item`), places geocoded through `DataStore.geocode` |
| route | cache mode: `FileRouteSource` reads `Route` JSON in `DataStore.routes_dir`, matched on origin/destination coordinates. OSRM mode: the harness passes the pipeline's router (`contracts.interfaces.Router`) and the oracle calls **only `.route()`** |
| candidate stations | every station in the store that is not `TEMPORARILY_UNAVAILABLE`/`PLANNED`, is `public`, has a usable port and lies within `retrieval.corridor_buffer_km` of the route. `max_candidates` is ignored (an efficiency cap, not a constraint) |
| position along route | own projection of the station onto the route polyline, scaled by `route.distance_km / polyline length` |
| detour minutes | own formula from `energy.yaml placeholder_detour`, in **every** routing mode: `fixed_min + 2 × offroute_km × road_factor / offroute_speed_kmh × 60` |
| leg distance i → j | `access_km(i) + (along_j − along_i) + access_km(j)`, with `access_km = offroute_km × road_factor` (0 at origin/destination) |

## Model the oracle implements
- **Energy.** `E_req = distance × efficiency × (1 + penalty)`, expressed as % of usable capacity.
  Every arrival at a station must be ≥ `reserve_soc_pct`. The destination arrival must be ≥ `max(reserve, arrival_soc_pct_min, or default_arrival_soc_pct when unstated)`.
  Departure is ≤ `charge_target_max_soc_pct`. A start SoC below the reserve is infeasible.
- **Charging time (estimate).** peak = `min(port power, vehicle max DC)`; AC ports × `ac_efficiency`. Constant power up to
  `taper_start_soc_pct`, then `taper_power_fraction × peak` (DC only). Plus `session_overhead_min` per stop.
- **Power.** Unknown power uses `charging_model.unknown_power_kw[level]` (overridable for the sensitivity runs) and never
  satisfies `min_power_kw`. A port with power ≤ 0 is unusable.
- **Compatibility (ADR 0012).** A native connector works at any level. A documented adapter works at DC fast only.
  `connectors_override` only narrows; an override with no overlap is infeasible.
- **LEAF (ADR 0009).** `nissan-leaf-sv-plus-2024` stays in feasibility metrics. Results carry `charge_time_excluded`, and the
  charge-time sensitivity excludes it.

## Outputs
- `is_feasible(request)` is **exact** and independent of the Pareto search. Departing every stop at the cap maximises reach.
  Labels (SoC, cumulative detour) are Pareto-pruned, so under `detour_scope="total"` a lower-reach, lower-detour path survives.
  Reason codes: `unknown_vehicle`, `connector_no_overlap`, `start_below_reserve`, `no_usable_station` (with excluded counts by
  reason), `energy_gap`, `arrival_soc_unreachable`, `detour_total_exceeded`. `connector_no_overlap` applies only when a stop is
  needed: a trip drivable without charging is feasible whatever the override. Requests with missing fields, an unresolved
  place, no route (`no_route`), a route source that raised (`route_error`, with the exception) or a degenerate route
  (`degenerate_route`: fewer than 2 points, zero length, e.g. origin and destination geocode to the same place) are
  `evaluable=False`. `FeasibilityResult` is truthy exactly when feasible.
- `pareto_plans(request)`: label-setting over stations in route order, with dominance pruning on (time, SoC, detour, charge).
  Departure targets are the exact need of every later node plus a `soc_grid_step_pct` grid. Returns the non-dominated plans over
  **(total time, detour, charge time)**. `complete=False` (with `incomplete_reason`) if the search inserts `max_labels`
  labels (`configs/evaluation.yaml`, 40,000 per search); never silently truncated. The label limit is deterministic:
  the same input and settings give the same `complete` flag, label count (`n_labels`) and front on any machine, so
  the items excluded from front-based metrics are the same in every run. `pareto_time_budget_s` (600 s) is only a
  wall-clock safety net; if it ever fires (`incomplete_reason="time_budget"`), the result depends on machine load and
  the planning metrics add an `oracle_nondeterministic_cutoff` finding.
  Calibration (2026-11-20, snapshot `93983c110571`, Deepti's items): every search that finished within the former
  60 s wall-clock limit used at most 34,960 labels, and deepti-010 had reached 39,432 when 60 s ran out. Throughput
  varies from about 200 to 2,300 labels/s by item, so no label count reproduces "60 s" item by item; 40,000 keeps
  every search that used to finish, and a search at the slowest rate now runs about 200 s at most.
  Results are cached per (request, settings) and returned as copies; the cache never changes an answer.
  Tested invariant: `is_feasible ⇔ front non-empty`, under both detour scopes, and every front plan passes `check_itinerary`.
- `check_itinerary(request, itinerary)` replays the plan with the oracle's own distances and lists **every** violation, using
  the contract codes `connector`, `power`, `detour`, `arrival_soc`, `reserve`, `status`, `access`, `departure_soc`, `continuity`,
  `unknown_station`, `route_distance`. Claim mismatches (claimed detour or arrival vs the oracle's) are notes, not violations.
  Power rules (`min_power_kw`, non-positive power) apply to the port the stop uses, identified by its claimed power.
  `ok` allows `soc_tol_pct` / `detour_tol_min`; `strict_ok` repeats the checks with the search's own EPS, so a plan that
  passes only within tolerance is known (`strict_violations`).
- Exact ties on the front (and between labels) keep the plan with fewer `UNKNOWN`-status stops, never the one whose station
  ID sorts first.

## Metrics (`metrics/planning.py`, wired into the harness)
plan feasibility rate, false rejection rate, hard-violation rate and counts by code, infeasible-handling accuracy (+ relaxed
plans checked against never-relaxed constraints of the original request AND against the relaxed request rebuilt from
`Relaxation.changes`), robust feasibility at +`efficiency_penalty_robust` (plan replay and oracle
re-run), unknown-power sensitivity (DC-fast fallback 25 and 100 kW vs 50, LEAF excluded), corridor-buffer diagnostic
(oracle-infeasible items re-checked with `configs/evaluation.yaml` `oracle.diagnostic_buffer_km`; feasibility can only gain
from extra stations, so feasible items are not re-run), and reliance on stations whose status is `UNKNOWN`.

Items the oracle cannot judge are never dropped silently: the feasibility block lists them (`items_skipped`,
`skipped_by_reason`: `clarification_required`, `no_result`, or the oracle's not-evaluable reason), and a run where no item is
evaluable gets an `oracle_nothing_evaluable` finding. The harness isolates each metric block: an exception becomes
`{"error": "<Type>: <message>"}` in that block.

Front-based metrics (front membership, oracle-fastest changes, unknown-status reliance, `planner_plan_outside_oracle_front`)
run only on oracle-feasible items where the planner returned a plan. An item whose search is incomplete is left out of all of
them (never judged against a partial front), gets an `oracle_search_incomplete` finding, and is counted in the
`oracle_search` block; the report lists the incomplete items per config.

## Disagreements are findings
`planning_metrics` returns `findings`, and the harness writes them to the top level of the results JSON:
- `oracle_feasible_planner_rejected`
- `oracle_infeasible_plan_passes_check`
- `planner_plan_outside_oracle_front` (a valid planner plan that no oracle plan matches within `pareto_tol_min`)
- `verifier_disagreement` (planner verifier vs the oracle check of the same, PARSED, request; the gold request only when the
  result has no parsed request or the oracle cannot evaluate it)
- `parse_changed_constraints` (gold vs parsed hard-constraint fields differ: the parser, not the verifier, is the cause)
- `plan_passes_only_within_tolerance` (passes `check_itinerary` only within tolerance; such a plan is never reported as
  `oracle_infeasible_plan_passes_check` or `planner_plan_outside_oracle_front`)
- `relaxation_unparseable` (`Relaxation.changes` not in the relaxation step's fixed forms; the relaxed plan counts as bad)
- `oracle_nothing_evaluable` (no item could be judged; the detail gives the reasons)
- `oracle_search_incomplete` (`max_labels` or the wall-clock safety net hit; the item is excluded from front-based metrics)
- `oracle_nondeterministic_cutoff` (the wall-clock safety net fired before `max_labels`: whether the item is excluded
  depends on machine load, so the run is not reproducible; raise `pareto_time_budget_s` or lower `max_labels`)
- `itinerary_uncheckable`

**Policy: never change the oracle to make it agree with the planner.** For each finding, first decide what the
documentation says, and fix the documentation if it is ambiguous (ADR if a frozen config or contract is involved). Then fix
whichever implementation is wrong, and add the case as a hand-built test.

## Gaps in the documentation and the decision taken
| # | Gap | Decision taken | Enforced in |
|---|---|---|---|
| 1 | ADR 0012 says an unsupported stated connector "is ignored" and that `[]` means no override. Read literally, an override with no overlap widens back to everything. The benchmark items ian-013, hriday-014, deepti-026 and hriday-025 expect infeasible. | **No overlap ⇒ infeasible** (`connector_no_overlap`). The unsupported part of a partly overlapping override is ignored. ADR 0012 is still *proposed*: fix the wording before approval. | `override_has_no_overlap`, `allowed_connectors` |
| 2 | `max_detour_min` per stop or for the whole trip | Parameter `detour_scope`, default `"stop"`; `"total"` is supported everywhere, including `is_feasible` | `OracleConfig.detour_scope` |
| 3 | Detour formula (energy.yaml gives only parameters). One-way vs round trip. TRADEOFFS' "3 min" example fits both (2.5 vs 3.1). | Round trip: `fixed_min + 2 × access_km / speed` | `detour_minutes` |
| 4 | Whether detour driving uses energy | Yes: `road_factor × offroute_km` each way is added to the adjacent legs | `_Problem.drop`, `check_itinerary` |
| 5 | Placeholder detour vs OSRM-measured detours in OSRM mode | The oracle always uses its own formula; detour checks allow `detour_tol_min` (1 min), route distance allows 5 %. Tolerances are a team decision | `OracleConfig` tolerances |
| 6 | Taper for AC sessions (TRADEOFFS says "(DC)", energy.yaml doesn't restrict) | Taper on DC only | `charge_minutes` |
| 7 | AC × 0.9 on top of EPA wall-to-wheels efficiency may double-count charging losses | Applied as documented; flagged for Hriday | `peak_kw` |
| 8 | `min_power_kw` against the port's power or the vehicle-capped power | The station's reported port power | `meets_min_power` |
| 9 | Status `UNKNOWN` (the contract default) | Usable. `PLANNED` and `TEMPORARILY_UNAVAILABLE` are excluded. Reliance is reported (`unknown_status` metrics) | `EXCLUDED_STATUSES` |
| 10 | Start below the reserve; `arrival_soc_pct_min` below the reserve | Start < reserve is infeasible; destination floor = `max(reserve, ·)` | `_problem`, `OracleConfig.floor_dest` |
| 11 | `charge_buffer_soc_pct` (2 %): hard constraint or policy? | Planner policy, not a constraint. The strict oracle never applies it; `OracleConfig.departure_buffer_pct` gives a separate policy front (after a charging stop, arrive at the next node that much above its floor, as the planner does), which `tradeoffs.pareto_validity` uses to tell buffer-only cases from real domination | `_targets`, `departure_buffer_pct` |
| 12 | Pareto dimensions: TRADEOFFS §4 and the docstring include amenity satisfaction, which needs matcher scores (retrieval) | Front over (time, detour, charge). `tradeoffs.pareto_validity` must not treat an alternative that is 3-D dominated but better on amenities as invalid | `pareto_plans` |
| 13 | Unknown-power sensitivity: which levels and values | DC fast only, 25 and 100 kW vs the 50 kW baseline; L2/L1 unchanged. Rank flip-rate under the cost model belongs in `metrics/tradeoffs.py` | `unknown_power_sensitivity` |
| 14 | "Robust feasibility" undefined | Both are reported: (a) valid recommended plans that still meet reserve and arrival when replayed at +15 %, (b) oracle-feasible items still feasible at +15 % | `robust_feasibility` |
| 15 | No data flag identifies the LEAF | Listed by vehicle id; a test checks the id exists in `data/curated/vehicles.csv` | `CHARGE_TIME_EXCLUDED_VEHICLES` |
| 16 | Naming of files in `routes/` undocumented | Routes matched on origin/destination coordinates (0.01°) | `FileRouteSource` |

### Observation for the planning owner
Under the placeholder model, total time = route duration + detour + charge time. On a single route the three Pareto
objectives therefore reduce to two (detour, charge). Once OSRM-measured detours replace the placeholder this stops being exact.
