# ADR 0016: Visible LLM fallback metadata

- Status: proposed
- Owner: Deepti (NLU); reviewers: Ian (evaluation), Hriday (pipeline/integration)
- Issue: #113

## Decision

When `LLMParser` falls back to the deterministic rules parser, it records:

- `ParseResult.parser = "rules_fallback"`;
- `ParseResult.fallback_reason` with a bounded category/message such as
  `unavailable: ReadTimeout: ...` or `invalid_output: attempt 2: ...`;
- the same values on `PlanResult.parser` and `PlanResult.fallback_reason`.

Successful LLM parses remain `parser="llm"` with no fallback reason. The fallback never invents
safety-critical fields.

Only endpoint/configuration failures (`requests.RequestException` and client `RuntimeError`) are
classified as unavailable. Invalid model output gets one repair attempt and then an
`invalid_output` fallback. Unexpected exceptions from parser code propagate instead of being
reported as an endpoint outage.

This is an additive contract change from v0.5 to v0.6. Existing saved results without these
optional fields remain valid. The evaluation parser trace continues to support older logs while
preferring the explicit result metadata.

## Consequences

- B/C evaluation can distinguish real LLM extraction from rules fallback.
- The CLI/API result exposes why a fallback occurred without relying on log scraping.
- Fallback messages may contain provider error text, so callers should treat them as diagnostics,
  not user-facing prose.
