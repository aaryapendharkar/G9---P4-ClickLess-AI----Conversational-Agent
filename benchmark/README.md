# benchmark/ (owner: Ian; everyone authors)

```
requests/raw/<author>.jsonl   # each member writes 30 items here (one JSON object per line)
requests/dev/                 # produced by evaluation.splits (50 items) + fixture_smoke.jsonl
requests/test/                # produced by evaluation.splits (100 items) -- FROZEN, never hand-edit
labels/relevance/             # graded relevance labels for nDCG@3 (30 test items, 2 annotators)
labels/double/<annotator>.jsonl  # blind double annotation, 8 items each (see labels/double/README.md)
FROZEN.json                   # hash + date of the test split, written at freeze time
```

- Schema: `src/evaluation/schema.py`; how to annotate: `docs/ANNOTATION_GUIDE.md`.
- Validate your file any time: `make bench-check`.
- Target mix per author (30): urban 5, rural 5, sparse_charging 4, multi_stop 4, incomplete 4,
  conflicting_prefs 3, accessibility 3, priority_tradeoff 2 (TA feedback). Infeasible cases can
  overlap any category; aim for ~2 each.
- Deepti (parser owner) writes items that go to DEV only (proposal §II).
- `dev/fixture_smoke.jsonl` uses the synthetic fixture world; it is NOT one of the 150 items.
  `load_items` skips `fixture_*.jsonl` when it reads a directory, so the dev split (and
  `make eval-dev`) is exactly the 50 frozen items; pass the file path to load the smoke items.
- Double annotation (`python -m evaluation.splits assign-double`): annotators in
  `configs/benchmark.yaml` `dev_only_annotators` (Deepti, the parser owner) only get dev items.
- Real items must use vehicles from `data/curated/vehicles.csv` and real places.
