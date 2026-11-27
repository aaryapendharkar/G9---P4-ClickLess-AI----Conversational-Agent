# Graded relevance labels for nDCG@3 (issue #184)

Relevance definition: Manas. Sampling, file schema, agreement and metric wiring: Ian.
Raters: Manas and Aarya, independently.

These labels judge **how good a returned plan is for the driver's request**. They are used only by
evaluation (nDCG@3 in `src/evaluation/metrics/planning.py`) and never by planning or ranking.
They are separate from the request-understanding double annotation in `labels/double/` (#175):
different question, different raters' files, different agreement numbers.

## What is judged

One **judgment** = one request text + one plan. The plans for a request are the **pool**: the
ranked top 3 of every configuration in the final TEST run (A, B, C and C-noKG), saved
(`SelectRecorder`, #192, #197), merged, with duplicates removed and the order shuffled. A plan is a
duplicate of another when it has the same ordered list of station ids. Plans not in the pool get
gain 0 in nDCG.

Labels are collected **after** the final TEST run, from its saved outputs. No configuration is run
again to produce or tune labels, and the scale below is fixed before any rater sees a pool.

## What a rater sees, and what they don't

A rater sees, for each item:

- the request `text`, exactly as in the benchmark;
- every pooled plan under a neutral id (`P1`, `P2`, ...), each with:
  - its stops in order: station name, connector, port power, charge minutes (estimate), detour
    minutes, arrival and departure battery %;
  - the amenities at each stop: name, category, wheelchair tag;
  - trip totals: drive minutes (including detours), charge minutes, detour minutes, number of
    stops, arrival battery %.

A rater does **not** see:

- which configuration returned the plan, or its rank in any configuration's list;
- `total_min` or any other `PlanCost` field (minutes-equivalent ranking scores). These come from the
  rankers being evaluated and differ by configuration, for example Config A's amenity-credit floor
  (ADR 0020). Showing them would put the system's own opinion into the judgment;
- matcher scores (`satisfied_preferences`), evidence or KG facts, or explanation text;
- the gold request or any system's parse.

## Scale

Grade each plan **on its own against the request**, using the other plans in the pool only where a
rule says "another pooled plan". Go down the list and stop at the first grade that applies.

| grade | name | rule |
|---:|---|---|
| 0 | not relevant | The plan breaks something the driver stated as a requirement: a detour limit, a connector, port power or network they named, an arrival battery % they gave, or an accessible restroom they said they **need** (only an amenity tagged `wheelchair=yes` counts). A plan that can't get the driver to the destination as shown also gets 0. |
| 1 | marginal | The requirements hold, but the plan misses what the request is mainly about: none of the requested amenities is present, or the driver asked for the fastest trip and another pooled plan with the same amenities is at least 15 minutes quicker (drive + charge). |
| 2 | relevant | The requirements hold and the plan is a reasonable answer, but not the best one: it misses some requested amenities, or another pooled plan with at least the same amenities is at least 5 minutes quicker (drive + charge), or it has more stops than another pooled plan that meets the same amenities. |
| 3 | highly relevant | The requirements hold, every requested amenity is present at some stop, and no other pooled plan is better by the grade-2 rules. Several plans for one request can be 3. |

### How to decide each part

- **Requested amenity present**: an amenity at one of the plan's stops has the right category, and
  its name doesn't contradict the request's wording. "Sit-down meal" isn't satisfied by a
  name that is clearly a drive-through only; "coffee" is satisfied by a café or a fast-food place
  that serves coffee if the name says so. When the name doesn't tell you either way, count it as
  present. Category alone decides for restrooms, except that "accessible", "wheelchair" or a stated
  need requires `wheelchair=yes` (`unknown` doesn't count).
- **Requirement vs. preference**: only what the driver states as a limit or a need is a
  requirement ("under 10 minutes of detour", "I need", "must", "only CCS"). "I'd like", "if
  possible" and "nice to have" are preferences, judged under grades 1–3, never 0.
- **Trade-off wording**: "in a hurry", "fastest" → compare time first. "No rush", "comfort",
  "good food" → a plan with the requested amenities beats a quicker plan without them; don't apply
  the time rules of grades 1–2 against a plan that lacks a requested amenity.
- **Charge minutes are estimates** for every plan alike; compare them as shown.
- **The thresholds** (5 and 15 minutes) are fixed here before labelling. 5 minutes is the same
  "meaningfully different" bar the system uses for alternatives (`configs/tradeoffs.yaml`,
  `min_time_diff_min`).
- **When unsure between two grades**, give the lower one and write why in `note`.

## Rater independence

- Each rater grades every pooled plan of every sampled item on their own, from the packet above,
  without running the system, opening the run's result files, or looking at the gold request.
- Raters don't discuss items, grades or plans until both files are submitted.
- If a rater wrote, cross-checked or double-annotated an item (#175), they say so in `note`. The
  sampling rule should avoid giving a rater items they wrote (see below).

## Agreement and adjudication

- Agreement is reported on the two raters' grades before adjudication: quadratic-weighted Cohen's κ
  and exact agreement over all pooled plans, plus the number of pairs that differ by 2 or more.
- The gain used in nDCG@3 is the **mean of the two grades** (so 0, 0.5, ..., 3).
- A pair that differs by **2 or more** is adjudicated: both raters discuss that plan only and agree
  on one grade, which replaces both. Each adjudication is recorded with the two original grades,
  the agreed grade and a one-line reason. Pairs that differ by 1 are not adjudicated.

## Sampling rule (fixed before the TEST run)

Ian, #184. This rule and its seed were fixed before the final TEST run (#187) and are applied
once, to that run's saved outputs. Nothing in it is changed after any TEST output has been seen.

**Eligible items.** A TEST item (`benchmark/requests/test/test.jsonl`, hash in `FROZEN.json`) is
eligible when both hold:

1. its `author` is neither rater (not `manas`, not `aarya`);
2. its **pool** has at least 2 distinct plans. The pool is the union of the saved `ranked_top3`
   of Configs A, B, C and C-noKG for that item. Two plans are the same plan when they have the same
   stations in the same order (`plan_key`, the ordered `station_ids`, as in `pooled_ndcg_at_k`,
   #197). A configuration that returned no plan for the item, or crashed, adds nothing to the pool.
   With fewer than 2 distinct plans, nDCG@3 is the same for every ranking and says nothing.

**Selecting 30 items** (seed **573**, the benchmark's freeze seed), stratified by the item's
`category` as evenly as possible:

1. Fixed category order: alphabetical (`accessibility`, `conflicting_prefs`, `incomplete`,
   `multi_stop`, `priority_tradeoff`, `rural`, `sparse_charging`, `urban`).
2. `rng = random.Random(573)`. For each category in that order: sort its eligible item ids, then
   `rng.shuffle` them (one generator, used in this order).
3. Round robin: go through the categories in the fixed order, taking the next item of each
   category's shuffled list, and repeat until 30 items are taken. A category with no items left is
   skipped, so a category that runs short is filled from the others in the same fixed order.
   With 8 categories and enough items, the first 6 in the order get 4 items and the last 2 get 3.
4. If fewer than 30 items are eligible, all of them are taken and the shortfall is reported. The
   eligibility rule is not loosened to reach 30.

The selected ids, per-category counts, eligible counts and the commit of the selection script are
recorded with the labels.

**What raters get.** For each selected item, the request `text` and the pool: deduplicated, config
and rank hidden, in a shuffled order under neutral ids `P1`, `P2`, ... (shuffle seeded with
`random.Random(f"573:{item_id}")`, so it is reproducible). When duplicates differ in anything shown
(for example charge minutes), the copy shown is the first one met going through A, B, C, C-noKG,
each in rank order. Raters see only the fields listed under "What a rater sees".

**Grades.** Both raters (Manas and Aarya) grade every pooled plan of every selected item, on their
own, on the 0–3 scale above. The gain in nDCG@3 is the mean of the two grades, after adjudication
as described above. Each configuration's nDCG@3 uses the pool's grades for its ideal ranking
(`pooled_ndcg_at_k`, #197).

**Saved fields.** `ranked_top3` in the run output already has everything the packet needs (#197):
per stop the station name, connector, port power, charge minutes, detour minutes and arrival /
departure battery %, plus trip totals (drive, charge and detour minutes, number of stops, arrival
battery %). `total_min` is saved for analysis but is left out of the packet.
