# ADR 0003: Energy reserve, default arrival SoC, accessibility as soft/hard

- Status: question 3 accepted (2 approvals: Ian 2026-11-22, Aarya 2026-11-23, on PR #148); questions 1–2 still proposed
- Owner: Hriday (energy), Deepti (accessibility semantics)
- Approvers: Ian, Aarya, Manas

## Questions
1. Reserve `E_reserve`: 10 % of usable capacity (current `energy.yaml`)?
2. Default arrival SoC when the user does not state one: 20 % (current)? Not safety-critical, so
   a default is allowed; the explanation must say it was assumed.
3. "Need a wheelchair-accessible restroom": soft preference with high weight (current) or a hard
   constraint? If hard, it can only be relaxed with explicit user consent — the frozen
   relaxation order currently relaxes amenities first.

## Decision
1. Reserve `E_reserve`: TBD.
2. Default arrival SoC: TBD.
3. **Accepted — Option A (hard requirement).** When a restroom amenity has both
   `wheelchair_required: true` and `priority: "important"`, planning must require a verified
   `wheelchair=yes` restroom at a stop. Unknown, limited, and non-accessible tags do not satisfy
   this requirement. If no qualifying plan exists, the system must ask the driver before relaxing
   the requirement; it must never silently drop it. A `nice_to_have` wheelchair restroom remains
   a soft ranked preference.

   Deepti's parser maps affirmative "need", "must", or "require" language to
   `priority: "important"` in both the Rules and LLM parsers; negated wording does not. The frozen
   request/gold representation remains unchanged (`amenities` with
   `wheelchair_required` and `priority`); this decision does not move amenities into
   `hard_constraints` or alter benchmark labels.

   **Implementation owners:** Deepti (parser and annotation wording); Hriday (planner enforcement,
   verifier, and consent-based relaxation); Ian (independent oracle parity). Accepted with the two
   required approvals on PR #148 (parser side merged). Planner enforcement, verifier and
   consent-based relaxation are tracked in #136 / #150.
