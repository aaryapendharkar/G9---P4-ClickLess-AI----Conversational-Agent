## What and why
<!-- one or two sentences; link the issue -->

## Module(s) touched
- [ ] only my own module (or my learning-swap file)
- [ ] touches `contracts/` → ADR in `docs/decisions/` + 2 approvals + `CONTRACTS_VERSION` bumped
- [ ] touches a frozen config/benchmark/snapshot → ADR, and I know which results this invalidates

## Checks
- [ ] `make test` passes; any xfail I fixed is now a normal test
- [ ] `make demo` still runs
- [ ] no API keys, raw data or results committed
- [ ] if a spec decision was made, it is written down (docstring or ADR), not only in chat
