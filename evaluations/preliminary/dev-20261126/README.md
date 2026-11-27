# DEV-only extract of run 20261126T024223Z (for #166 / PR #180)

`recommended_amenity_scores.csv`: one row per (config, DEV item, requested amenity) for Configs A, B and C,
taken from the **Recommended** itinerary of each saved DEV result. `best_score` is the matcher score of the
best stop for that preference (A: raw TF-IDF; B/C: SBERT), `0.0` meaning no match. `amenity_credit_min` is the
itinerary's total amenity credit as saved (A is raw, pre-floor). Source: `results/20261126T024223Z/*_dev_*.json`
(split `dev`, 50 items, snapshot `snapshot:96dfbabb4837`). The held-out TEST split is not included.

Summary over rows with `best_score > 0`: B and C each n=19, mean 0.527, median 0.516 (identical, same recommended
plans); A raw n=9, mean 0.421, median 0.462.
