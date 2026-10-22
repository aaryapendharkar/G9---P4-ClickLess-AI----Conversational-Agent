# ClickLess Charge

Provenance-aware conversational agent for EV charging planning — CSE 573 Semantic Web Mining,
Fall 2026, Group 9 (Deepti Ravi Kumar, Aarya Balkrishna Pendharkar, Manas Tole, Hriday Pradhan,
Ian Mello).

One natural-language request → a verified charging itinerary (connector-compatible, battery-
feasible under a documented energy model) → an explanation with sources and trade-offs → a
Google Maps handoff **only after the driver approves**.

## Status: walking skeleton
Configuration A runs end-to-end on a tiny **synthetic** Phoenix → Flagstaff world, so everyone
can build their module against working neighbours from day one. Most components are deliberately
naive placeholders or stubs; each one's docstring lists its TODOs and the tests mark open specs
as `xfail`. See `docs/ARCHITECTURE.md` → *Implementation status*.

```bash
make setup
make test     # passes + xfails (xfail = an open TODO with an owner)
make demo
```

```
Stop at Fixture Camp Verde Hub (CCS1, 50 kW): charge 26% -> 43% in about 18 min (estimate).
  Nearby (requested): Verde Fixture Kitchen, Verde Fixture Restroom
Arrive in Flagstaff with about 22% after ~161 min total (3 min of detour).
Why this ranks first (balanced priorities, in minutes-equivalent): driving 143 + charging 18 + ...
Other options:
  Fastest (Fixture Plaza Anthem): -10 min total; ... loses food, restroom_wheelchair.
Open this route in Google Maps? (approval required)
```

## Repository map

| Path | What | Owner |
|---|---|---|
| `src/clickless/contracts/` | shared Pydantic types + stage Protocols (v0.1) | everyone (2 approvals) |
| `src/clickless/data/`, `handoff/`, `data/` | AFDC/OSM/vehicle ingestion, provenance, snapshot; Maps URLs + approval gate | Aarya |
| `src/clickless/nlu/`, `explain/` | rules/spaCy/LLM extraction, clarification, priority phrases; explanations | Deepti |
| `src/clickless/retrieval/` | corridor retrieval, TF-IDF/SBERT matching, ontology + KG | Manas |
| `src/clickless/planning/`, `ranking/` | routing, energy, charging-time estimate, filters, planner, verifier, relaxation; cost + trade-offs | Hriday |
| `src/evaluation/`, `benchmark/` | benchmark schema/splits, independent oracle, metrics, statistics, harness | Ian |
| `src/clickless/pipeline.py` | Fig. 1 orchestration, config wiring | weekly integration captain |
| `configs/` | A/B/C wiring; energy, trade-off, relaxation, retrieval parameters | see CODEOWNERS |
| `docs/` | team plan, architecture, trade-off design, annotation guide, ADRs | — |

## Commands
| | |
|---|---|
| `python -m clickless plan "<request>" [--config A\|B\|C] [--json]` | plan one request (asks before handoff) |
| `make eval-dev CONFIG=A` | run the harness on the dev split → `results/` |
| `make todo` | every open spec and its owner |
| `make bench-check` | validate all benchmark JSONL files |

## Read next
1. `docs/TEAM_PLAN.md` — ownership, learning swaps, captain rotation, revised timeline
2. `docs/ARCHITECTURE.md` — pipeline, config wiring, dependency rules
3. `docs/TRADEOFFS.md` — response to the TA's trade-off feedback
4. `CONTRIBUTING.md` — how to work without stepping on each other
