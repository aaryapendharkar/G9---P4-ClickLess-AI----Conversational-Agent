# ADR 0017: Config C explanation scope

- Status: proposed
- Owner: Deepti
- Approvers: Ian (evaluation), Hriday (integration)
- Related issue: #129

## Decision

For the current demo and evaluation, Config C uses deterministic template explanations with
machine-checkable verified-plan and KG evidence claims. Config C does not generate free-form
explanation prose with an LLM.

The Config C and C-noKG descriptions are aligned to say `evidence-grounded template explanation
with trade-offs`. The runtime components, parser, planner, ranking behavior, evidence semantics,
and previous evaluation results are unchanged. This is a description-only scope clarification.

A future LLM paraphrase or free-text claim-checking layer requires a separate decision, prompt
version, evaluation plan, and latency/fallback tests.

## Consequences

- Reports must describe C as template-based and evidence-grounded, not LLM-generated.
- Evidence coverage and claim-support metrics measure traceability/consistency, not free-form LLM
  explanation quality.
- The C-noKG ablation remains comparable because its only runtime difference is KG retrieval.
- No frozen runtime result needs to be invalidated by this wording-only change.
