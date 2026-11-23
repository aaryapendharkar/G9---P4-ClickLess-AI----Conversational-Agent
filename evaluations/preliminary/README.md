# PRELIMINARY dev-set results: not final numbers

Everything in this folder comes from the **dev split**, from a shakedown run made **before** several fixes were
merged. These numbers are for the progress presentation only. Do not cite them as results. The final numbers come
from a full dev re-run and then the sealed 100-item test split (`../README.md`).

## `2026-11-21_dev_deepti30/`: shakedown on 30 dev items

| | |
|---|---|
| Items | Deepti's 30 benchmark items (`benchmark/requests/raw/deepti.jsonl`). All 30 are in the frozen dev split. |
| Configs | A, B, C (`report.md` + CSVs); C-noKG ablation in `C-noKG/` |
| Run | 2026-11-21, snapshot, `qwen3:8b` at the pinned digest, Ollama 0.40.17 |
| Code | A and B on `f019c87` (working tree not clean), C and C-noKG on `ab93502`. Both commits are on a local evaluation branch that includes the oracle from PR #74. |

**Known caveats**
- **Run before #133** (route-distributed candidates). #133 changes which stations reach the planner, and the
  feasibility numbers here will change on a re-run.
- **Run before the parser fixes** #148 (explicit "need" amenities, natural battery answers) and #149 (no invented
  connector requirements). The CCS1 constraint invented on deepti-024 (`findings.csv`) is fixed by #149.
- **Field F1 uses strict place matching** (it requires the state suffix). That is why origin/destination F1 is low;
  at city level, place F1 is 0.98 for C. Current code reports city-level matching as the primary score.
- **Plan feasibility has no CI in the report.** The figure uses a Wilson 95% interval on k/25 oracle-feasible items.
- **30 items from one author**, so the CIs are wide.

Headline (from `headline.csv`, 95% CI where available):

| Config | Clarification F1 | Field F1 (strict) | Plan feasibility | Hard violations | LLM fallbacks |
|---|---|---|---|---|---|
| A | 0.42 [0.12, 0.67] | 0.58 [0.53, 0.63] | 12/25 = 0.48 | 0 of 14 itineraries | n/a (rules) |
| B | 0.62 [0.20, 0.89] | 0.61 [0.56, 0.66] | 17/25 = 0.68 | 0 of 22 | 0/30 |
| C | 0.62 [0.20, 0.89] | 0.61 [0.56, 0.66] | 17/25 = 0.68 | 0 of 22 | 0/30 |

- **Clarification, A vs B:** 6 items better, 0 worse (McNemar p = 0.031). B and C share the same parser, so their
  understanding metrics are identical.
- **Grounding (C):** 57/57 claims supported; 18 cite KG evidence (6 KG only, 12 KG + verified data). C-noKG: 51/51
  supported, none citing the KG.

## `figures/`

| File | Shows | Source |
|---|---|---|
| `eval_a_configs_ABC.png` | A vs B vs C: clarification F1, field F1, plan feasibility, with 95% CIs | `2026-11-21_dev_deepti30/` |
| `eval_b_133_before_after.png` | #133 before/after: false "infeasible" 7 → 0, planner–oracle agreement 36/43 → 43/43 | PR #133 before/after table: 43 requests (the 41 evaluable dev items + 2 demo trips), Config A on gold requests, checked against the oracle |
