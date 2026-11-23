# ADR 0018: Route-distributed candidates, at most 2 usable per stretch (+ required amenities)

- Status: accepted (2 approvals: Hriday 2026-11-22, Aarya 2026-11-23, on PR #133). Decision 4 (required-amenity
  retention) was added on 2026-11-23 at Hriday's request (#136); his re-review on PR #133 is required before merge.
- Date: 2026-11-21
- Owner: Ian (evaluation)
- Approvers: Hriday (planning, integration), Aarya (data, evaluation); two approvals needed (`docs/TEAM_PLAN.md`)
- Related: #106, #105, PR #133, #134 (closed), #136 / ADR 0003 (required accessibility), #153 (port-aware ranking)

## Context
`CorridorRetriever` hands the planner at most `max_candidates: 60` stations. Before #133 it kept
the first 60 by `along_route_km`. With a dense origin, all 60 sat at the start of the route: 494
snapshot stations are within 1 km of downtown Los Angeles, so on LA → SF all 60 were within
2.1 km. The planner then saw no later charger, and long trips came back "infeasible" when they
weren't (#105, #106). On the 41 dev items + 2 demo trips, the planner disagreed with the oracle
(#74) on 7 of 43, and every disagreement was a false "infeasible".

Spreading the 60 slots along the route fixes this. How to pick within a stretch, and how many
stations to let through, decides both plan quality and planning time:
- **Nearest-first or fastest-first within a ~25 km stretch**, ranked by raw station data. In review,
  Hriday showed a fast charger the car can't use (wrong connector, restricted or unavailable) can
  push out a slower usable one.
- **Usable-first:** only stations that pass the planner's station checks (status, access,
  connector, minimum power) compete. The planner then gets up to 60 stations it can actually use,
  and its search time explodes (max 356 s).
- **Usable-first with a bound:** a smaller overall cap (30 or 40), or at most k usable stations
  per stretch.

Measured on the 41 dev items + 2 demo trips (LA → SF, Phoenix → Tucson), Config A, snapshot. Each
returned plan is checked with the oracle's `check_itinerary`, and each status against the oracle's
feasibility. "Rec" is the recommended plan's total trip time.

| Variant | Planner–oracle agreement | False "infeasible" | Rec vs main: faster / slower (worst) | Rec vs fastest-first: slower (worst) / net | Planning s median / p90 / max | > 30 s |
|---|---|---|---|---|---|---|
| main (first 60 along the route) | 36/43 | 7 | – | – | 0.08 / 0.30 / 2.1 | 0 |
| fastest-first per stretch | 43/43 | 0 | 11 / 1 (+3.1) | – | 0.67 / 11.2 / 29.1 | 0 |
| nearest-first per stretch | 43/43 | 0 | 12 / 3 (+8.7) | 7 (+30.3) / +37.8 min | 0.17 / 1.3 / 6.7 | 0 |
| usable-first, cap 60 | 43/43 | 0 | 13 / 2 (+3.1) | 2 (+2.0) / −31.1 min | 6.55 / 39.0 / 356.3 | 7 |
| usable-first, cap 40 | 43/43 | 0 | 13 / 2 (+3.1) | 1 (+2.0) / −24.5 min | 1.59 / 4.3 / 20.7 | 0 |
| usable-first, cap 30 | 43/43 | 0 | 13 / 2 (+3.1) | 3 (+2.0) / −24.0 min | 0.64 / 2.7 / 4.4 | 0 |
| **usable, k = 2 per stretch** | **43/43** | **0** | **15 / 3 (+3.1)** | **4 (+4.1) / −40.1 min** | **0.12 / 1.4 / 6.2** | **0** |
| usable, k = 3 per stretch | 43/43 | 0 | 14 / 3 (+3.1) | 3 (+3.6) / −37.2 min | 0.15 / 2.2 / 26.6 | 0 |
| usable, k = 4 per stretch | 43/43 | 0 | 14 / 1 (+3.1) | 1 (+3.6) / −38.7 min | 0.19 / 2.5 / 21.5 | 0 |

Every returned itinerary passes the oracle check in every variant. Only fastest-first and
nearest-first can let an unusable station take a usable one's slot.

## Decision
`CorridorRetriever` selects candidates in `select_distributed` (`src/clickless/retrieval/corridor.py`):
1. **Usable stations only.** The pipeline injects `usable(station, request)`, which wraps
   `DefaultHardConstraintFilter.station_rejection` (status, access, connector, minimum power).
   Retrieval imports nothing from planning or the oracle. With no vehicle on the request, every
   station counts as usable.
2. **At most `max_usable_per_stretch: 2` per stretch** (`configs/retrieval.yaml`, shared by A/B/C).
   The route is split into ~25 km stretches (`DEFAULT_BIN_KM`); in each, the 2 usable stations
   with the fastest port are kept (ties: route order). This applies even when the corridor is
   under the cap.
3. **Cap of 60 kept.** If the bounded usable set still exceeds 60, it is shared round-robin across
   the stretches, fastest first. Stations the car can't use fill any free slots in route order;
   the hard-constraint filter removes them before planning, as before.
4. **Required amenities are retained** (`max_required_extra_per_stretch: 2`, #136). Ranking by power
   can drop the only station with something the driver needs: in demo scenario 4, the one charger
   with a `wheelchair=yes` restroom (Flagstaff Chevrolet) lost its slot to faster chargers. After
   steps 1–3, a usable station is added back when an amenity within `amenity_radius_m` satisfies a
   preference the request marks `important`: same category, and `wheelchair=yes` when
   `wheelchair_required` (the planner's rule, ADR 0003). At most 2 are added per stretch, closest
   to the route first, on top of the cap of 60. `nice_to_have` and `other` preferences are never
   retained (an `other` match is the matcher's decision, made after retrieval). A request without
   a required preference skips this step, so its candidates are exactly those of steps 1–3.

k = 2 is chosen because:
- it has the best net plan time of all the variants;
- it is the fastest (max 6.2 s; LA → SF 4.0 s, Phoenix → Tucson 0.1 s);
- it has no false "infeasible".

Runner-up: k = 4 (one item slower than fastest-first instead of four; max 21.5 s).

### Re-run with decision 4 (2026-11-23, on main `85173fe` + #133)
Same 41 dev items + 2 demo trips, same method (Config A on gold requests, snapshot, oracle from #74
@ `c566ff3`).

| Variant | Planner–oracle agreement | False "infeasible" | Returned plans failing the oracle check | Planning s median / p90 / max |
|---|---|---|---|---|
| main (first 60 along the route) | 36/43 | 7 | 0 | 0.07 / 0.30 / 2.5 |
| k = 2 per stretch (decisions 1–3) | 43/43 | 0 | 0 | 0.09 / 0.80 / 5.4 |
| **k = 2 + required-amenity retention (1–4)** | **43/43** | **0** | **0** | **0.09 / 2.58 / 5.5** |

Decision 4 changes the candidates of exactly the 8 requests with an `important` amenity (1–16 extra
stations each). The other 35 have identical candidates. Recommended plans: 3 unchanged, 2 faster
(aarya-026 and deepti-027, −7.5 min), deepti-024 −0.2 min, and 2 that now stop for the required
restroom (deepti-005 +2.0 min, deepti-010 +6.6 min).

## Consequences
- **Results:** the cut changes candidate sets on 38 of 43 items and the recommended plan on 25:
  7 newly feasible, 15 faster and 3 slower than main. The 3 slower are deepti-022 (+3.1 min), deepti-006 (+1.4 min)
  and deepti-008 (+0.1 min). Every Config A/B/C result produced before #133 used the old cut;
  re-run any dev result that is reported.
- **Tuned on dev only.** k = 2 and `DEFAULT_BIN_KM = 25` were chosen on the 41 dev items + 2 demo
  trips against the oracle. They were never evaluated on the test split. They stay frozen for the
  held-out run; changing them afterwards needs a new ADR and a fresh dev comparison.
- **Fewer near-equivalent options on dense urban stretches.** A 25 km city trip gets at most 2
  usable candidates (plus any slower ones that fit within other stretches' quotas). Across the dev
  set, the number of alternatives shown went from 51 (fastest-first) to 48. aarya-001 lost its
  second option.
- **Planner latency is bounded by retrieval, not fixed in the planner.** #134 is closed for now.
  Raising k or removing the bound brings the exponential search back. Reopen #134 first.
- **The check lives in one place.** `station_rejection` is shared with the filter, so retrieval
  and planning cannot disagree about what "usable" means.
- **Tests:**
  - `tests/retrieval/test_distributed_candidates.py`: selection, both retrieval methods, Hriday's
    unusable-station regressions with and without the bound, and the per-stretch bound;
  - `tests/integration/test_long_route_candidates.py`: deepti-014/015 and LA → SF plan and verify
    on the snapshot;
  - the snapshot indexed/brute-force equivalence tests are unchanged;
  - required-amenity retention: unit tests on both methods (kept beyond the k = 2 picks, bounded,
    closest first, `wheelchair=yes` only; unchanged candidates without an important amenity), and
    `tests/integration/test_required_amenity_candidates.py` (scenario 4 keeps Flagstaff Chevrolet).
- **Planning time:** retention adds at most 2 stations per stretch for requests with a required
  amenity; the dev p90 rises from 0.8 to 2.6 s, max 5.5 s.
