# Contributing

## Setup
```bash
python -m venv .venv && source .venv/bin/activate
make setup            # pip install -e ".[dev]"
pip install -e ".[nlp]"  # only for B/C Sentence-BERT matching; first use downloads the pinned model (~90 MB)
cp .env.example .env  # only needed for B/C (Voyager) and live AFDC/OSRM
make test && make demo
```

## How we avoid merge conflicts
1. **Stay in your folders** (see `.github/CODEOWNERS`). Need something from another module?
   Open an issue for its owner, or add an optional field to a contract (cheap) — don't edit
   their code.
2. **Contracts first, code second.** Changing `src/clickless/contracts/` = ADR in
   `docs/decisions/` + 2 approvals + bump `CONTRACTS_VERSION`. Prefer *adding optional fields*;
   avoid renames/removals.
3. **Small PRs** (< ~300 lines), one topic, merged within 2 days. Rebase on `main` before merge.
4. **Configs are separate files** so tuning one module never conflicts with another.
5. Benchmark items: one JSONL file per author — never edit someone else's file.
6. `pipeline.py` changes go through the week's integration captain.

## Branches and commits
- Branch: `<name>/<module>-<short-topic>` e.g. `aarya/data-afdc-ingest`, `manas/planning-adapters` (swap).
- Commit message: imperative, module prefix: `planning: add detour check to verifier`.
- `main` is protected: CI green + code-owner approval.

## Tests as the TODO list
- Unfinished behaviour is written as `@pytest.mark.xfail(reason="TODO(<owner>): ...")`.
- When you implement it, the test shows **XPASS** → delete the `xfail` line in the same PR.
- `make todo` lists every open spec with its owner.
- Integration test `tests/integration/test_walking_skeleton.py` must always pass on `main`.

## Data, keys, results
- Never commit `.env`, `data/raw/`, `data/snapshot/*` (except `MANIFEST.json`) or `results/`.
- Synthetic fixtures are for tests only; never report numbers from them.
- After the test freeze: the test split, snapshot, `energy.yaml`, `tradeoffs.yaml` and
  `relaxation.yaml` change only via ADR, and the ADR lists which results must be re-run.

## Decisions (ADRs)
Copy `docs/decisions/0000-template.md`. Status: proposed → accepted (at the Thursday sync).
