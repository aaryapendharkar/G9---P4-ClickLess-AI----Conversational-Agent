# evaluations/final/

Results of the held-out **test** split (100 items), run once with `scripts/final_run.ps1 -Split test -Final`.

| Path | What |
|---|---|
| [`20261128T100314Z/`](20261128T100314Z) | the cited final report, copied unchanged from `results/20261128T100314Z/`: `report.md`, its CSVs and `report_inputs.json` |
| [`20261128T100314Z_recomputed/`](20261128T100314Z_recomputed) | **recomputed** report from the same saved outputs, nothing re-run: built later with `evaluation.report --from` at the code of #185/#186, so it adds handoff correctness (`handoff.csv`), latency p50/p95 (`latency.csv`) and provenance completeness (`provenance.csv`). All other CSVs are byte-identical to the original copy. Cite the original for every earlier number |
| [`ANALYSIS.md`](ANALYSIS.md) | read-only analysis of that run: paired tests, Config A's hard violations, clarification F1, silent assumptions, breakdowns, oracle cutoffs |

Final run: `results/20261128T100314Z`, commit `e810f93` (clean `main`), snapshot `snapshot:96dfbabb4837`, all four
configs (A, B, C, C-noKG), no failed configs. The per-config result JSONs (about 4 MB) were not copied. They stay
in the run folder, and `python -m evaluation.report --from <folder>` rebuilds the report from them. It writes
`report.md` and the CSVs into the folder it reads, so run it on a copy (the result JSONs, `*_handoff_audit.jsonl`
and `report_inputs.json`), never on the run folder itself. That is how `20261128T100314Z_recomputed/` was made,
with `--latency-note "one Windows laptop (RTX 2060 6 GB; qwen3:8b ~70% on GPU, rest on CPU)"`.

## Aborted attempt

The first test attempt, `results/20261128T095754Z`, was stopped by hand (Ctrl-C, `KeyboardInterrupt` in
`A.log`) about one minute after it started, during Config A, to fix the machine's power settings. No result
JSON was written, and no results were read before the run was stopped. The final run above was started right
after it.
