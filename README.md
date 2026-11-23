# ClickLess Charge

Provenance-aware conversational agent for EV charging planning — CSE 573 Semantic Web Mining,
Fall 2026, Group 9 (Deepti Ravi Kumar, Aarya Balkrishna Pendharkar, Manas Tole, Hriday Pradhan,
Ian Mello).

One natural-language request → a verified charging itinerary (connector-compatible, battery-
feasible under a documented energy model) → an explanation with sources and trade-offs → a
Google Maps handoff **only after the driver approves**.

```
PS> python -m clickless plan --config C "From Downtown Los Angeles to Santa Monica in my Model 3 Long Range, I have
    25% battery and want to arrive with at least 20%, and I'd like a coffee shop while I charge"
Stop at Los Angeles, CA - Industrial Street - Tesla Supercharger (NACS, 250 kW): charge 24% -> 28% in about 4 min (estimate).
  Source: AFDC, cached snapshot (source updated 2026-06-18)
  Nearby (requested): Daily Dose, Cafe Dulce Dos, Go Get Em Tiger
Arrive in Santa Monica with about 22% after ~33 min total (9 min of detour).
Why this ranks first (balanced priorities, in minutes-equivalent): driving 29 + charging 4 + detour penalty 5 + ...
Other options:
  Least detour: stop at 550 S. MAIN ST. - TOPAZ - Tesla Destination instead; about 11 min longer; ... loses coffee.
Open this route in Google Maps? (approval required)
```

## What the system does

1. **Understand the request:** origin, destination, vehicle, battery now and on arrival, connectors, power, detour
   limit, amenities and their priority, trade-off profile ("fastest", "no rush"). If a safety-critical value is
   missing, Config C asks for it instead of guessing.
2. **Retrieve** charging stations along the route from a versioned snapshot of AFDC stations and OSM amenities. Config C
   adds evidence from a knowledge graph.
3. **Plan and verify:** a deterministic planner builds itineraries that are connector-compatible and battery-feasible,
   and an independent verifier checks them.
4. **Rank and explain:** the recommended plan plus alternatives (fastest, best amenities, …), with every number
   traceable to the plan and every claim to its source.
5. **Hand off:** a Google Maps link, only after the driver approves an option.

## Configurations

All three share the same planner and verifier; they differ in understanding, retrieval and explanation
(`configs/config_{a,b,c}.yaml`).

| Config | Summary |
|---|---|
| **A**, classical baseline | rules extraction, TF-IDF matching, logistic-regression ranker, template explanation; rejects incomplete requests |
| **B**, modern language stack | LLM extraction (Qwen3 8B, local via Ollama), Sentence-BERT matching, weighted ranking, template explanation |
| **C**, hybrid | B + KG evidence, clarification questions, source and freshness labels, evidence-grounded explanation |

`configs/config_c_nokg.yaml` (**C-noKG**) is the ablation without the KG. See [`evaluations/`](evaluations) for how
the configurations are compared.

## Status (Nov 22)

- **A, B and C run end-to-end** on the shared real-data snapshot (v1 draft; the final frozen build comes before the
  test run).
- **The 150-item benchmark is frozen:** 50 dev / 100 test.
- **Preliminary dev-set results** are in [`evaluations/preliminary/`](evaluations/preliminary).
- **In progress:**
  - the independent oracle and planning metrics (#74);
  - hard enforcement of "need" accessibility requirements (#136);
  - route-distributed candidates (#133).

Open specs are marked `xfail` in the tests (`make todo`).

## Repository layout

| Course category | Where | What |
|---|---|---|
| **CODE** | [`src/`](src) | `src/clickless/`: the system (pipeline, NLU, retrieval + KG, planning, ranking, explanation, handoff). `src/evaluation/`: benchmark tooling, independent oracle, metrics, statistics, report |
| | [`scripts/`](scripts) | one-off build scripts (e.g. the route cache) |
| | [`configs/`](configs) | configuration A/B/C wiring and the energy, trade-off, relaxation and retrieval parameters |
| **DATA** | [`data/`](data) | synthetic test fixtures, curated vehicle table, the snapshot manifest. The snapshot itself is downloaded (see below) |
| | [`benchmark/`](benchmark) | the 150 annotated requests, the frozen 50 dev / 100 test split, double annotations |
| **EVALUATIONS** | [`evaluations/`](evaluations) | methodology (oracle, metrics, split, statistics), how to reproduce, and the reports we cite |
| other | [`tests/`](tests), [`docs/`](docs) | unit/integration tests; architecture, team plan, annotation guide, demo script, design decisions (`docs/decisions/`) |

Owners by module:

| Path | What | Owner |
|---|---|---|
| `src/clickless/contracts/` | shared Pydantic types + stage Protocols | everyone (2 approvals) |
| `src/clickless/data/`, `handoff/`, `data/` | AFDC/OSM/vehicle ingestion, provenance, snapshot; Maps URLs + approval gate | Aarya |
| `src/clickless/nlu/`, `explain/` | rules/spaCy/LLM extraction, clarification, priority phrases; explanations | Deepti |
| `src/clickless/retrieval/` | corridor retrieval, TF-IDF/SBERT matching, ontology + KG | Manas |
| `src/clickless/planning/`, `ranking/` | routing, energy, charging-time estimate, filters, planner, verifier, relaxation; cost + trade-offs | Hriday |
| `src/evaluation/`, `benchmark/`, `evaluations/` | benchmark schema/splits, independent oracle, metrics, statistics, harness, reports | Ian |
| `src/clickless/pipeline.py` | orchestration, config wiring | weekly integration captain |

## Setup

```bash
pip install -e ".[dev,nlp,kg]"   # nlp: Sentence-BERT (B, C); kg: rdflib (C)
pytest                           # = make test; xfail = an open TODO with an owner
```

**Snapshot (real data).** It isn't in git. Download `snapshot-v1-draft.zip` from the repository's GitHub release
`snapshot-v1-draft` (or the team drive) and unzip it into `data/snapshot/`, so that `data/snapshot/MANIFEST.json`,
`stations.json` and `routes/` exist. Then verify it:
```bash
python -m clickless.data.snapshot_report --verify-only   # = make snapshot-verify; must print: verify_manifest() -> [] (intact)
```
The app refuses to load a snapshot that doesn't match its manifest. Building a snapshot yourself is described in
`data/snapshot/README.md`.

**LLM (Configs B and C).** Run Ollama with `qwen3:8b` at the pinned digest (`docs/VOYAGER_LOCAL.md`). The app doesn't
read `.env` by itself; set the variables in the shell:
```powershell
$env:CLICKLESS_DATA_SOURCE = "snapshot"            # default is the synthetic fixtures
$env:VOYAGER_BASE_URL = "http://localhost:11434/v1"
$env:VOYAGER_API_KEY = ""
# Bash: export CLICKLESS_DATA_SOURCE=snapshot; set -a; source .env; set +a
```

## Running it

| | |
|---|---|
| `python -m clickless plan --config C "<request>"` | plan one request; asks clarification questions and asks before the Maps handoff |
| `python -m clickless plan --config A "<request>" --json` | the full `PlanResult` as JSON; `--no-input` never prompts |
| `make demo` | Config A on the synthetic fixture world (no snapshot or LLM needed) |
| `docs/DEMO_SCRIPT.md` | the six demo scenarios on the snapshot, with setup and warm-up |

Only trips whose route is in the snapshot's route cache can be planned.

## Evaluating it

```bash
python -m evaluation.report --configs A B C --split dev   # = make eval-dev; or make eval-dev CONFIGS="A B"
python -m evaluation.report --configs C-noKG --split dev  # KG ablation
python -m evaluation.report --from results/<timestamp>    # rebuild a report from saved runs
make bench-check                                          # validate every benchmark file
```
Runs write `results/<timestamp>/report.md` + CSVs (git-ignored). The held-out test split needs `--final`. The
methodology and how to reproduce are in [`evaluations/README.md`](evaluations/README.md).

## Read next
1. `docs/ARCHITECTURE.md`: pipeline, config wiring, dependency rules
2. `docs/TEAM_PLAN.md`: ownership, learning swaps, captain rotation, timeline
3. `docs/TRADEOFFS.md`: response to the TA's trade-off feedback
4. `evaluations/README.md`: evaluation methodology
5. `CONTRIBUTING.md`: how to work without stepping on each other
6. `docs/VOYAGER_LOCAL.md`: local Qwen setup and model comparison
