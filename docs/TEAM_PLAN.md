# Team plan: who owns what, how we learn from each other, how we avoid conflicts

## 1. Ownership (unchanged from proposal §VII, mapped to folders)

Each person **owns** folders. Owners merge into their folders; nobody else edits them except
through a reviewed PR (CODEOWNERS enforces this).

| Member | Owns (folders) | Proposal responsibilities | Config A must-haves (by Oct 28) |
|---|---|---|---|
| **Deepti** | `nlu/`, `explain/` | rules/spaCy/LLM extraction, clarification, grounded explanation, UI | rules parser handles your 30 dev phrasings; template wording |
| **Aarya** | `data/`, `handoff/`, `data/` folder | AFDC ingestion, connector normalization, vehicle table, OSM amenities, provenance, Maps handoff | snapshot v0 (AZ subset is fine) in fixture layout; 15 curated vehicles with source URLs |
| **Manas** | `retrieval/` (incl. `kg/`) | ontology + CQs, KG, corridor retrieval, TF-IDF/SBERT, KG ablation | spatial index; real TF-IDF matcher; ontology v0.2 |
| **Hriday** | `planning/`, `ranking/` | routing + energy, filters, ranking, verifier (+ trade-offs) | OSRM detours (cached), verifier power/detour checks, multi-stop planner |
| **Ian** | `src/evaluation/`, `benchmark/` | benchmark protocol, oracle, harness, ablations, statistics | split + freeze tooling, oracle `is_valid`, extraction metrics |

Shared files (`contracts/`, `common/`, `pipeline.py`, frozen configs) need **2 approvals** and
an ADR (`docs/decisions/`) when they change behaviour.

## 2. Learning swaps (everyone learns a second module)

The ring follows the data flow, so each person learns the module that *consumes* or *feeds*
theirs. Each swap is one small, real task inside the buddy's module. The owner reviews it.
You are also an optional reviewer on all your buddy's PRs for the semester — read them, ask
questions; that is most of the learning.

```
Aarya ──► Manas ──► Hriday ──► Deepti ──► Ian ──► Aarya
 data      KG/retrieval  planning   explanation  evaluation
```

| Learner → module | Swap task (file) | Done when | Why it's useful |
|---|---|---|---|
| Aarya → Manas's KG | Write competency queries CQ2–CQ6 in SPARQL (`retrieval/kg/competency_queries/`) | each query returns the expected rows on the fixture KG | you see how your normalized data + provenance become a graph |
| Manas → Hriday's planning | Adapter support in `compatible_ports` (`planning/filters.py`) | `test_adapter_makes_station_compatible` passes | your `supports`/`supportsViaAdapter` ontology becomes a hard constraint |
| Hriday → Deepti's explanation | Trade-off sentences (`explain/tradeoff_presenter.py`) | templates read naturally; numbers only from `Alternative` fields | you learn what a driver needs to understand from your cost model |
| Deepti → Ian's evaluation | Handoff metrics (`evaluation/metrics/handoff.py`) | URL parse/order/segment checks with tests | evaluation without grading your own component |
| Ian → Aarya's data | Snapshot validator (`data/validate.py`) | `test_fixture_store_passes_validation` passes; feeds provenance-completeness | you know exactly what the frozen data guarantees before the oracle uses it |

Rules that keep the evaluation honest:
- Deepti does not implement request-understanding metrics (no self-grading).
- **Hriday never reviews or reads `evaluation/oracle/`** before the test run; Manas reviews it.
- Ian writes the oracle from the documented energy model, not from the planner's code
  (`tests/test_architecture.py` blocks imports).

## 3. The TA's trade-off feedback is a shared feature (each in their own files)

| Who | Piece | File |
|---|---|---|
| Hriday | minutes-equivalent cost, charging-time estimate, Pareto alternatives, diverse plans from the planner | `ranking/cost.py`, `ranking/tradeoffs.py`, `planning/charging_time.py`, `planning/planner.py` |
| Deepti | priority phrases → profile/overrides; "prioritize X" follow-up re-ranks; C's grounded trade-off sentences | `nlu/priority_phrases.py`, `explain/grounded.py` |
| Manas | match scores that feed the amenity credit; KG evidence that feeds the evidence credit | `retrieval/*_matcher.py`, `retrieval/kg/enrich.py` |
| Aarya | port power + vehicle max DC kW with provenance; flag unknown power | `data/afdc.py`, `data/vehicles.py` |
| Ian | Pareto validity, delta faithfulness, weight + charge-time sensitivity; 2 `priority_tradeoff` items per author | `evaluation/metrics/tradeoffs.py`, `benchmark/` |

Full design: `docs/TRADEOFFS.md`.

## 4. Integration captain (rotates weekly — everyone learns the whole pipeline)

| Week starting | Captain | What lands that week |
|---|---|---|
| Thu Oct 22 | Aarya | repo setup, GitHub handles in CODEOWNERS, branch protection, contracts v0.1 review |
| Mon Oct 26 | Ian | harness on dev, Config A pieces; class presentation Oct 28 |
| Mon Nov 2 | Deepti | Config B (LLM parser) |
| Mon Nov 9 | Manas | Config C (KG, clarification, grounding) |
| Mon Nov 16 | Hriday | held-out evaluation + ablations; planner fixes |
| Mon Nov 23 | Aarya | demo freeze, handoff tests, final report assembly |

Captain duties: keep `main` green (fix or revert within a day), decide merge order when PRs
touch neighbours, run `make demo` + `make eval-dev` on Thursday, post a 5-line status
(done / blocked / numbers / next / decisions needed).

## 5. Rhythm

- **Mon, 15 min async standup** in the group chat: yesterday / today / blocked.
- **Thu, 30 min integration sync** led by the captain: demo `main`, decide open ADRs.
- **Pairing hour** once a week with your learning buddy (screen-share on their module).
- Decisions live in `docs/decisions/` — if it was decided in chat, someone writes the ADR.

## 6. Revised timeline (we are ~1 week behind the proposal; this recovers by Nov 8)

The proposal planned Config A + oracle + **test-set freeze by Oct 21**. The freeze is the one
thing we cannot let slip further: nothing may be tuned before it.

| By | Milestone | Owner(s) |
|---|---|---|
| **Sat Oct 24** | everyone runs `make test` + `make demo`; contracts v0.1 reviewed and tagged; ADRs 0001–0003 decided (KG store, reserve/arrival defaults, accessibility = soft/hard) | all |
| **Sun Oct 25** | 15 benchmark items each in `benchmark/requests/raw/` | all |
| **Wed Oct 28** | **class presentation**: architecture, walking-skeleton demo, trade-off design (answers the TA), benchmark status | Ian (captain) + all |
| **Sun Nov 1** | 30 items each, cross-checked; 40 double-annotated; **split + test freeze**; snapshot v1 frozen; learning swaps merged | Ian, Aarya, all |
| Sun Nov 8 | Config A on full dev with real components; Config B evaluated on dev; relevance labels collected | all (proposal week 9–10) |
| Sun Nov 15 | Config C: KG enrichment, clarification, grounded explanation incl. trade-offs, relaxation; UI + handoff tests | proposal week 11 |
| Fri Nov 20 | held-out evaluation, ablations, LLM-planner comparison, trade-off sensitivity, statistics; demo frozen | proposal week 12 |
| Wed Dec 9 | demo, failure analysis, final report, code release | all |

## 7. Day-one checklist (each person)

1. Clone, `make setup`, `make test` (expect passes + xfails), `make demo`.
2. Read `docs/ARCHITECTURE.md` and your folder's module docstrings (they list your TODOs).
3. `make todo` → your xfail specs are your first issues. Open a GitHub issue per spec.
4. Read your learning buddy's module docstrings; book the first pairing hour.
5. Start your 30 benchmark items using `docs/ANNOTATION_GUIDE.md`.
