# ADR 0004: Precise verifier violation codes

- Status: accepted
- Date: 2026-10-24
- Owner: Hriday
- Approvers: Ian, Aarya

## Context

The complete-itinerary verifier must independently reject unavailable or restricted stations,
impossible claimed departure battery levels, and broken ordering between legs and charging
stops. The shared `ViolationCode` contract has a code for station status, but it has no precise
codes for station access, departure state of charge, or itinerary continuity.

Reusing unrelated codes such as `reserve` would make explanations and Ian's failure-analysis
metrics unable to distinguish energy failures from malformed itineraries. Changing a shared
contract requires an ADR, two approvals, and a `CONTRACTS_VERSION` bump.

## Decision

Add three values to `ViolationCode`:

- `access`: a station is not publicly accessible.
- `departure_soc`: a claimed departure state of charge is below the recomputed arrival value or
  above `charge_target_max_soc_pct`.
- `continuity`: leg counts, endpoint labels, or stop ordering do not form one continuous trip.

Keep `status` for unavailable or planned stations. Change legs/stops count mismatches from the
unrelated `reserve` code to `continuity`. Bump `CONTRACTS_VERSION` from `0.1.0` to `0.2.0` because
the change is additive: no existing code or field is removed or renamed.

## Consequences

- Existing serialized verification reports remain valid.
- Consumers may optionally handle the three new codes; existing handling of old codes is
  unaffected.
- Explanations and evaluation can categorize failures without parsing free-form detail text.
- The verifier still receives planner-produced leg distances and detour values. Recomputing those
  from the authoritative route is a separate interface decision and is not part of this ADR.
- Tests must cover status, access, departure limits, and continuity before this ADR is accepted.
