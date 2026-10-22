# ADR 0002: Trade-offs weighted in minutes-equivalent, presented as Pareto alternatives

- Status: proposed
- Owner: Hriday
- Approvers: Deepti, Ian

## Context
TA feedback asks how detour, charging duration and amenities are weighted and presented. The
proposal used "normalized" weights, which are hard to explain to a driver or in the report.

## Decision (proposed)
Rank verified plans by a minutes-equivalent cost (docs/TRADEOFFS.md §2) with three profiles in
`configs/tradeoffs.yaml`; charging time is a labelled estimate (§3); show Recommended plus up to
two Pareto-optimal alternatives with deltas taken from verified fields (§4).

## Consequences
`PlanCost` and `Alternative` added to contracts v0.1. Weights tuned on dev and frozen before
test. New metrics: Pareto validity, delta faithfulness, weight and charge-time sensitivity. The
report replaces "normalized weighted ranking" with this description.
