# Blind double annotation (40 items, issue #49 follow-up)

One file per second annotator, 8 items each, from `python -m evaluation.splits assign-double`
(seed 573; Deepti, the parser owner, only gets dev items, see `configs/benchmark.yaml`).
Each line has the item `id` and request `text` only; every annotation field starts empty.

## How to submit
1. Make a branch `<you>/double-annotation` from `main`.
2. Edit **only** `benchmark/labels/double/<you>.jsonl`. For each line, fill in `category`,
   `gold_request`, `clarification_required`, `missing_safety_fields`, `hard_constraints`,
   `soft_constraints` and (if useful) `notes`, following `docs/ANNOTATION_GUIDE.md`.
   Keep `id` and `text` exactly as they are, one JSON object per line.
3. Open a PR "benchmark: double annotation (<you>)". Ian checks the format only.

## Blind rule
Annotate from `text` alone. **Do not open the original item (`requests/raw/`, `requests/dev/`,
`requests/test/`) or its gold before your PR is submitted**, and don't discuss these items with
their author or cross-checker until then. If you saw an item's gold earlier (e.g. while
reviewing), say so in that line's `notes`.
