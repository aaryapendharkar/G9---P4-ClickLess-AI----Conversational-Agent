# Contributing

## Setup

Run these commands from the repository root. Direct Python commands are canonical; GNU Make is optional.

### Windows PowerShell

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,nlp,kg]"

# Only needed for Configs B/C (Voyager) or live AFDC/OSRM settings.
Copy-Item .env.example .env

python -m pytest
python -m clickless plan --config A --no-input "From Phoenix to Flagstaff in my Spark LR, I have 65% battery and want to arrive with at least 20%, lunch stop and a wheelchair accessible restroom please"
```

If activation is blocked, run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned`
and activate again. This changes policy only for the current PowerShell process.

### macOS and Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,nlp,kg]"

# Only needed for Configs B/C (Voyager) or live AFDC/OSRM settings.
cp .env.example .env

python -m pytest
python -m clickless plan --config A --no-input "From Phoenix to Flagstaff in my Spark LR, I have 65% battery and want to arrive with at least 20%, lunch stop and a wheelchair accessible restroom please"
```

The `nlp` extra installs the pinned Sentence-BERT dependency used by Configs B/C; the `kg` extra
installs rdflib for Config C and the competency queries. Where GNU Make is installed, `make setup`,
`make test` and `make demo` remain optional aliases. Windows contributors do not need Make.

After downloading the frozen snapshot, verify it directly with
`python -m clickless.data.snapshot_report --verify-only`. Validate the benchmark files with:

```text
python -c "from evaluation.schema import load_items; [print(split, len(load_items('benchmark/requests/' + split))) for split in ('raw', 'dev', 'test')]"
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
