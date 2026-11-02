# ADR 0006: Surface temporary OSRM verifier fallback

- Status: accepted
- Date: 2026-11-01
- Owner: Hriday
- Approvers: Ian, Aarya

## Context

The strict OSRM client and cache-first router correctly distinguish temporary
service failures from invalid input, malformed responses, and an authoritative
`NoRoute` result. `TemporaryFailureFallbackMeasurer` can already replace a
failed OSRM verifier measurement with the deterministic geometric estimate,
while recording the selected source and an estimate warning.

The shared pipeline does not yet construct that policy or copy its warning into
the returned `PlanResult`. Consequently, the tested policy cannot currently
protect an OSRM-mode verification run or tell the driver that estimated values
were used.

## Decision

When routing mode is `osrm`, the pipeline will give the verifier a
`TemporaryFailureFallbackMeasurer` configured with:

- `OSRMRouteMeasurer` as the primary strategy, using the existing cache-first
  OSRM router; and
- `GeometricRouteMeasurer` as the deterministic fallback strategy, using the
  configured energy and detour assumptions.

After the final verification of the recommended plan, the pipeline will append
the policy warning to `PlanResult.notes` exactly once when geometric fallback
was used. A successful OSRM measurement will add no warning.

Only `OSRMTemporaryError` is fallback-eligible. `NoRoute`, invalid coordinates,
non-retryable HTTP failures, malformed responses, and configuration errors will
continue to propagate instead of being disguised as estimates.

This decision covers verifier route measurements only. The initial
origin-to-destination route and candidate-detour measurements used during
planning remain strict and require separate policies because geometry cannot
construct a missing base road route.

No shared contract shape changes, so `CONTRACTS_VERSION` is not bumped.

## Consequences

- Cached and live OSRM measurements remain preferred and authoritative.
- Temporary verifier outages can complete with an explicit estimate warning.
- Configuration A and all committed cache-mode configurations remain unchanged.
- Pipeline behavior changes, so this ADR requires two approvals before merge.
- Integration tests must cover construction of both strategies and propagation
  of the warning from the final recommended-plan verification.
- Planner-side candidate-detour fallback remains a separate milestone.
