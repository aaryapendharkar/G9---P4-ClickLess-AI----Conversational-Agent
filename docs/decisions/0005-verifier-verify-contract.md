# ADR 0005: Authoritative route measurements in itinerary verification

- Status: accepted
- Date: 2026-10-24
- Owner: Hriday
- Approvers: Ian, Aarya

## Context

The verifier currently recomputes energy use from each itinerary leg, but the
leg distance and charging-stop detour are supplied by the planner. A malformed
planner output can therefore understate distance or detour and make an
infeasible itinerary appear feasible.

The verifier needs the authoritative Route so it can independently derive
Configuration A route measurements from cached route geometry and station
coordinates.

## Decision

Add `route: Route` to `Verifier.verify`.

Add `route_distance` to `ViolationCode` for a declared leg distance that does
not match the independently measured route distance.

The verifier will independently derive:

- station position along the cached route;
- estimated off-route road distance;
- expected leg distance;
- expected detour time;
- expected total detour time.

The verifier will use recomputed leg distances for its energy calculations and
recomputed detours for the driver's maximum-detour constraint.

Bump `CONTRACTS_VERSION` from `0.2.0` to `0.3.0`.

## Consequences

- Pipeline calls must supply the route to the verifier.
- Tests that call the verifier directly must also supply the route.
- Configuration A remains deterministic and offline.
- The measurement remains an explicitly labelled estimate.
- Live OSRM waypoint routing remains a separate task.