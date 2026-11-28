# evaluations/

This folder holds the project's **evaluation methodology and results**. The evaluation *code* lives in
[`src/evaluation/`](../src/evaluation) and the benchmark items in [`benchmark/`](../benchmark); this folder explains how
they fit together and keeps the reports we cite.

| Path | What |
|---|---|
| `README.md` | methodology and how to reproduce (this file) |
| [`preliminary/`](preliminary) | **preliminary** dev-set results and figures. Not final numbers; see that folder's README |
| [`final/`](final) | **final** results on the held-out test split: the cited report and its analysis |

## What is compared

All configurations share the same deterministic planner and verifier. They differ in how the request is understood,
which evidence is retrieved and how the answer is explained (`configs/config_*.yaml`).

| Config | Parser | Matching | Retrieval | Incomplete request | Explanation |
|---|---|---|---|---|---|
| **A**, classical baseline | rules | TF-IDF | corridor | rejected | template |
| **B**, modern language stack | LLM (Qwen3 8B, local) | Sentence-BERT | corridor | rejected | template |
| **C**, hybrid | LLM | Sentence-BERT | corridor + KG | clarification question | evidence-grounded, with source labels |
| **C-noKG**, ablation | as C | as C | corridor only | as C | as C |

The LLM runs locally through Ollama at a pinned model digest (`docs/VOYAGER_LOCAL.md`). Every run records the git
commit, model digest, Ollama version, prompt version and snapshot id (`metadata.csv`).

## Benchmark

- **150 requests**, 30 per team member, with a fixed mix per author: urban, rural, sparse charging, multi-stop,
  incomplete, conflicting preferences, accessibility, priority trade-off. Each item has a gold structured request
  (`docs/ANNOTATION_GUIDE.md`, schema in `src/evaluation/schema.py`).
- **Frozen split: 50 dev / 100 test.** Seed 573; the split was frozen on 2026-11-21 (`benchmark/FROZEN.json` stores
  the SHA-256 of each split file). Rules (`src/evaluation/splits.py`):
  - The parser owner's items go to dev only.
  - Items with the same origin–destination pair never straddle the split.
  - The test set keeps the overall category and route-type mix.
- **The test split is held out.** `evaluation.report` refuses it without `--final`, and all tuning uses dev.
- **40 items are double-annotated blind** to measure inter-annotator agreement (Cohen's kappa, field agreement;
  `src/evaluation/agreement.py`).

## Independent oracle

`src/evaluation/oracle/` is written without importing the planner's code. It reads the same inputs as the planner (snapshot,
cached routes) and its own implementation of the documented energy model.
- **Label-setting search:** labels are (route position, elapsed time, SoC, path), and dominated labels are pruned.
  The search decides whether *any* itinerary satisfies all hard constraints, and returns the Pareto set over
  (time, detour, charge, amenities).
- **`is_valid(itinerary)`:** checks every itinerary the system returns.
- **Disagreements** between planner and oracle are reported as findings (`findings.csv`). Items where the search
  stops early are excluded from the oracle-based metrics and listed in `oracle_search.csv`.

> Status: the full independent oracle and planning metrics are implemented on `main`. Final
> Config A/B/C reports must be generated from the frozen snapshot and benchmark split; preliminary
> reports produced before later planner and ranking decisions remain historical evidence only.

## Metrics

| Area | Metrics | Code |
|---|---|---|
| Request understanding | field-level P/R/F1 (micro and per field), exact match, clarification F1, silent-assumption rate, invented-constraint rate | `metrics/request_understanding.py` |
| Planning | plan feasibility (oracle-feasible items that get an oracle-valid plan), hard-violation rate (every returned itinerary checked against the **gold** request, so parser errors that lead to a bad plan count; the report also gives the number of planner-verifier/oracle disagreements), infeasible-request handling, robustness under +15% consumption, nDCG@3 | `metrics/planning.py` |
| Trade-offs | Pareto validity of the shown alternatives, faithfulness of trade-off numbers, sensitivity to weights | `metrics/tradeoffs.py` |
| Grounding | claim-support rate (every cited evidence id must support the claim), KG evidence coverage (share of the recommended plan's displayed stop facts supported by the result's own KG evidence), KG-cited claim share, freshness-label errors | `metrics/grounding.py` |
| Handoff | the approved Google Maps URL contains each approved stop exactly once | `metrics/handoff.py` |

**Statistics** (`metrics/stats.py`):
- 95% percentile-bootstrap CIs (10,000 resamples, seed 573), resampling items. Plan feasibility and the
  hard-violation rate use the per-item planning outcomes the harness saves (pooled ratio for violations,
  so an item's itineraries stay together).
- Paired tests between configs on the items both ran: McNemar for per-item success, Wilcoxon signed-rank for
  per-item scores. Pairs: A vs B, A vs C, B vs C, and C vs C-noKG for the KG ablation. McNemar also covers
  "got a valid plan" (oracle-feasible items); C vs C-noKG adds Wilcoxon on KG evidence coverage and the
  KG-cited claim share.

## How to reproduce

Setup (repo root): `pip install -e ".[dev,nlp,kg]"`, the shared snapshot unzipped into `data/snapshot/` and verified, and
Ollama running `qwen3:8b`. See the root `README.md`.

```powershell
$env:CLICKLESS_DATA_SOURCE = "snapshot"
$env:VOYAGER_BASE_URL = "http://localhost:11434/v1"
$env:VOYAGER_API_KEY = ""
python -m evaluation.report --configs A B C C-noKG --split dev                 # -> results/<timestamp>/
python -m evaluation.report --configs A B C --items benchmark/requests/raw/deepti.jsonl   # a subset, e.g. one author
python -m evaluation.report --from results/<timestamp>                         # rebuild report.md + CSVs from saved runs
python -m evaluation.planning_items results/<timestamp>                        # older runs: per-item planning outcomes (oracle only), then --from
python -m evaluation.report --configs A B C C-noKG --split test --final        # final run only, after all tuning
```
Bash: `export CLICKLESS_DATA_SOURCE=snapshot`, then `make eval-dev` (all of A, B, C) or `make eval-dev CONFIGS="A B"`.

The data source matters. The configs default to the synthetic fixture world, where the benchmark's real places and
vehicles don't exist. So a `--split` run without `CLICKLESS_DATA_SOURCE=snapshot` stops before any item runs with
"Evaluation needs the snapshot: set CLICKLESS_DATA_SOURCE=snapshot (see README)" (#119). Explicit `--items` runs are
not checked; the fixture smoke file (`--items benchmark/requests/dev/fixture_smoke.jsonl`) is meant for the fixtures.

### Full run: `scripts/final_run.ps1`

The full dev run and the final test run use `scripts/final_run.ps1`. Run it yourself from a plain PowerShell
window at the repo root, not from inside an agent session:

```powershell
$env:CLICKLESS_DATA_SOURCE = "snapshot"
$env:VOYAGER_BASE_URL = "http://localhost:11434/v1"
powershell -ExecutionPolicy Bypass -File scripts\final_run.ps1 -Split dev           # dev: as often as needed
powershell -ExecutionPolicy Bypass -File scripts\final_run.ps1 -Split dev -Limit 5  # smoke run: first 5 dev items
powershell -ExecutionPolicy Bypass -File scripts\final_run.ps1 -Split test -Final   # test: once
```

`-Limit N` runs only the first N items of the split, as a quick check before a full run. It is refused with `-Final`.
A `-Limit` smoke run may also run from another branch or a dirty tree, with a yellow "smoke run, not reproducible"
warning. A full run, and every `-Final` run, needs a clean `main`.

It aborts before running anything unless all of these hold:
- you are on `main` with a clean tree, untracked files included (it prints the commit);
- `CLICKLESS_DATA_SOURCE=snapshot`;
- the snapshot verifies (`[]`) and its id is `snapshot:96dfbabb4837` (frozen v1);
- Ollama answers at `VOYAGER_BASE_URL`, `qwen3:8b` has digest `500a1f067a9f…`, and a warm-up call succeeds.

It then runs A, B, C and C-noKG one at a time, each in its own process, logging to `results/<timestamp>/<config>.log`.
If a config crashes, the script says which one and runs the rest. At the end it builds the combined report
(`evaluation.report --from`) and prints its path, the headline table and the total time. It exits with 1 if any
config or the report failed.

**The test split runs once.** All tuning, debugging and reruns happen on dev. The script refuses `-Split test`
without `-Final`. It also refuses to start if `results/` already holds a final test run. If a test run is aborted
(crash, power loss), agree with the team before rerunning it, and record the reason in `final/` next to the report.
The cited test report is copied into `final/` unchanged.

Each run writes `report.md`, `headline.csv`, `paired.csv`, `breakdown.csv` (by author, category, route type),
`errors.csv`, `findings.csv` and `metadata.csv` under `results/<timestamp>/`. `results/` is git-ignored; copy a
report here when it is cited.
