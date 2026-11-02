# ADR 0008: Use routed legs during itinerary construction

- Status: accepted
- Date: 2026-11-01
- Owner: Hriday
- Approvers: Ian, Aarya

## Context

The planner's forward search and final itinerary simulation currently derive
leg distance and duration from cached geometry, a road factor, and average route
speed. In OSRM mode, the verifier independently checks the resulting itinerary
against cache-first routed waypoint measurements. A planner itinerary can
therefore be rejected merely because construction and verification used
different measurement semantics.

The forward search still needs inexpensive estimates while exploring many stop
sequences. Final simulation, however, already replays each proposed sequence
and is the appropriate boundary for authoritative routed measurements.

## Decision

Inject an `ItineraryRouteMeasurer` into `ItineraryPlanner`. For each proposed
stop sequence, the planner will obtain one `ItineraryRouteMeasurement` before
final simulation. Simulation will use its:

- leg distance for energy and state-of-charge calculations;
- leg duration for itinerary driving time; and
- stop detour values for charging stops and total detour.

`MeasuredLeg` will carry both `distance_km` and `duration_min`. The geometric
strategy will derive duration using the existing average-speed and detour
formula; the OSRM strategy will retain the routed duration returned by OSRM.

The planner and verifier will receive separate measurement-strategy instances
so verification remains independent. Both may share the cache-first route
provider, allowing identical cached facts without sharing control flow or
planner-produced values.

The forward label search remains geometric and proposes candidate sequences.
Final routed simulation may reject a proposal that its cheaper search estimate
considered feasible. Planner measurement warnings will be collected and copied
to `PlanResult.notes` exactly once.

No shared contract shape changes, so `CONTRACTS_VERSION` is not bumped.

## Consequences

- Constructed itinerary legs and verifier measurements use compatible routing
  semantics in OSRM mode.
- Energy, driving time, detour totals, and charging needs reflect the selected
  final measurement strategy.
- Planner and verifier strategy objects remain independent.
- OSRM cache reuse prevents repeated successful waypoint requests from always
  contacting the live service.
- Final simulation performs additional route measurements for proposed stop
  sequences, increasing work compared with geometric-only construction.
- Shared pipeline behavior changes, so this ADR requires two approvals.
