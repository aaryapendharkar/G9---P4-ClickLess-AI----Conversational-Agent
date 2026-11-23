# Blind double annotation (40 items, issue #49 follow-up)

40 items (8 per author, one per category) are re-annotated blind for Cohen's κ / field agreement.
One file per second annotator, from `python -m evaluation.splits assign-double` (seed 573) with
the rules in `configs/benchmark.yaml`:

| annotator | items | why |
|---|---:|---|
| aarya | 11 | |
| hriday | 10 | |
| manas | 11 | |
| deepti | 8 | dev items only, capped at 8: she owns the parser, the same reason her own items are all in dev |
| ian | 0 | excluded: see below |

Nobody gets an item they wrote or cross-checked. Each line has the item `id` and request `text`
only; every annotation field starts empty.

## Why Ian is not a second annotator
Ian is the benchmark owner and has seen nearly every item's gold: he reviewed the cross-check
PRs (including the full review of Manas's 30 items in #102) and ran the split and freeze checks.
His annotations would not be blind, so `excluded_annotators: [ian]` takes him out of the
assignment. His own 8 items are still double-annotated by the others.

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
