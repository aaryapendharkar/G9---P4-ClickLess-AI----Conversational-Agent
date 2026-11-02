# ADR 0007: Surface temporary OSRM candidate-detour fallback

- Status: accepted
- Date: 2026-11-01
- Owner: Hriday
- Approvers: Ian, Aarya

## Context

ADR 0006 covers route measurements used by final verification. Planning also
measures a detour for every retrieved charging-station candidate before hard
constraints, itinerary construction, and ranking. A temporary OSRM failure in
that earlier stage currently aborts planning even though the existing geometric
detour estimate can provide a conservative, explicitly labelled fallback.

One planning attempt measures several candidates. Fallback state must therefore
survive later successful candidate measurements so the final result still
discloses that mixed measurement sources were used.

## Decision

Keep the cache-first OSRM router strict. Add a separate
`TemporaryFailureDetourMeasurer` that:

- delegates candidate detours to the cache-first OSRM router first;
- catches only `OSRMTemporaryError`;
- delegates eligible failures to `CachedRouter.detour_min`;
- retains a fallback warning for the complete candidate collection; and
- resets that state before the next planning attempt.

The pipeline will use this policy only in OSRM mode and append its warning to
`PlanResult.notes`. Cache mode continues to use `CachedRouter` directly.
`NoRoute`, invalid input, malformed responses, non-retryable HTTP failures, and
configuration errors remain visible.

No shared contract shape changes, so `CONTRACTS_VERSION` is not bumped.

## Consequences

- Temporary failures for individual candidate reroutes no longer abort the
  complete planning attempt.
- Mixed OSRM/geometric candidate collections are explicitly disclosed.
- The strict router remains reusable by consumers that do not allow fallback.
- Shared pipeline behavior changes, so this ADR requires two approvals.
- Base-route failure remains strict because a detour estimate cannot create a
  missing origin-to-destination route.
