# evaluations/final/

Results of the held-out **test** split (100 items), run once with `scripts/final_run.ps1 -Split test -Final`.

| Path | What |
|---|---|
| [`20261128T100314Z/`](20261128T100314Z) | the cited final report, copied unchanged from `results/20261128T100314Z/`: `report.md`, its CSVs and `report_inputs.json` |
| [`ANALYSIS.md`](ANALYSIS.md) | read-only analysis of that run: paired tests, Config A's hard violations, clarification F1, silent assumptions, breakdowns, oracle cutoffs |

Final run: `results/20261128T100314Z`, commit `e810f93` (clean `main`), snapshot `snapshot:96dfbabb4837`, all four
configs (A, B, C, C-noKG), no failed configs. The per-config result JSONs (about 4 MB) were not copied. They stay
in the run folder, and `python -m evaluation.report --from results/20261128T100314Z` rebuilds the report from them.

## Aborted attempt

The first test attempt, `results/20261128T095754Z`, was stopped by hand (Ctrl-C, `KeyboardInterrupt` in
`A.log`) about one minute after it started, during Config A, to fix the machine's power settings. No result
JSON was written, and no results were read before the run was stopped. The final run above was started right
after it.
