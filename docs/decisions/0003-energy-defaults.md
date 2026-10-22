# ADR 0003: Energy reserve, default arrival SoC, accessibility as soft/hard

- Status: proposed — decide at the Oct 24 meeting
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
TBD. Write the outcome here, update `configs/energy.yaml` / `configs/relaxation.yaml`, and
mirror it in the annotation guide.
