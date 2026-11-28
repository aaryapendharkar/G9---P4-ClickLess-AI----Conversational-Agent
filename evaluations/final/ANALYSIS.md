# Final test run: analysis

Run `results/20261128T100314Z` (`-Split test -Final`), commit `e810f93` (clean `main`), snapshot
`snapshot:96dfbabb4837` (frozen v1), `qwen3:8b` digest `500a1f067a9f…`, Ollama 0.40.18, prompt `extract_v2`.
100 test items, all four configs, no failed configs, no errors, no LLM fallbacks.

This is a read-only analysis of the saved result JSONs. Nothing was re-run, and no number in `report.md` or the CSVs
was changed. The headline numbers below come from that report. The extra tests and breakdowns were computed from the
same per-item rows with the report's own functions (`evaluation.report.load_runs` / `per_item`,
`evaluation.metrics.stats.mcnemar` / `wilcoxon`, `evaluation.metrics.request_understanding`).

## Headline (from `report.md`)

| Config | Field F1 | F1 (strict) | Exact match | EM (strict) | Clarification F1 | Silent assumption | Invented constraint | Plan feasibility | Hard-violation rate |
|---|---|---|---|---|---|---|---|---|---|
| A | 0.863 [0.836, 0.890] | 0.682 | 0.380 [0.29, 0.48] | 0.160 | 0.369 [0.208, 0.514] | 0.000 | 0.010 | 0.386 [0.271, 0.500] | 0.310 [0.167, 0.463] |
| B | 0.934 [0.910, 0.956] | 0.745 | 0.720 [0.63, 0.81] | 0.270 | 0.373 [0.204, 0.523] | 0.083 | 0.000 | 0.571 [0.457, 0.686] | 0.000 |
| C | = B | = B | = B | = B | = B | = B | = B | = B | = B |
| C-noKG | = B | = B | = B | = B | = B | = B | = B | = B | = B |

For C and C-noKG, grounding (claim support) is 1.000 and the freshness mislabel rate is 0.000. KG evidence
coverage is 1.000 for C and 0.000 for C-noKG; the KG-cited claim share is 0.494 for C and 0.000 for C-noKG. The
parse and planning error rates are 0 for every config.

## 1. Paired tests

The tests follow the report's method: McNemar on per-item successes (exact binomial when there are fewer than 25
discordant pairs, otherwise continuity-corrected chi-square), and Wilcoxon signed-rank on per-item scores, over the
items both configs ran. `report.md` covers exact match, clarification correctness, no error, no invented
constraint, valid plan, field F1 and latency. Marked † are the per-item forms of the other headline metrics,
added here:
- strict EM and strict field F1;
- not silent, over the 12 gold-incomplete items;
- no hard violation: McNemar over items where both configs returned at least one itinerary;
- violating share: Wilcoxon on the per-item share of violating itineraries.

Clarification F1 and the hard-violation rate are pooled ratios. Their paired form is the per-item outcome:
clarification decision correct, or item has no violating itinerary.

### A vs B (A vs C is identical, because C = B on every per-item outcome)

| Outcome | n | A | B | b (A only) | c (B only) | p | |
|---|---|---|---|---|---|---|---|
| Exact match | 100 | 0.380 | 0.720 | 3 | 37 | 1.8e-7 | **significant** |
| Exact match (strict) † | 100 | 0.160 | 0.270 | 1 | 12 | 0.0034 | **significant** |
| Field F1 (Wilcoxon) | 100 | 0.856 | 0.936 | 59 non-zero | | 7.9e-7 | **significant** |
| Field F1 strict (Wilcoxon) † | 100 | 0.681 | 0.749 | 56 non-zero | | 5.0e-6 | **significant** |
| Got a valid plan (oracle-feasible) | 70 | 0.386 | 0.571 | 1 | 14 | 0.00098 | **significant** |
| No hard violation † | 34 | 0.765 | 1.000 | 0 | 8 | 0.0078 | **significant** |
| Violating share (Wilcoxon) † | 34 | 0.235 | 0.000 | 8 non-zero | | 0.0047 | **significant** |
| Clarification decision correct | 100 | 0.590 | 0.630 | 3 | 7 | 0.34 | not significant |
| Not silent (incomplete items) † | 12 | 1.000 | 0.917 | 1 | 0 | 1.0 | not significant |
| No invented constraint | 100 | 0.990 | 1.000 | 0 | 1 | 1.0 | not significant |
| No error | 100 | 1.000 | 1.000 | 0 | 0 | 1.0 | no difference |
| Latency (Wilcoxon) | 100 | 865 ms | 11 077 ms | 100 non-zero | | 3.9e-18 | B slower, significant |

The p-values above are uncorrected. With Holm over this family of 11 tests (counting one hard-violation test),
the seven significant results stay significant; the largest adjusted p is 0.039, for no hard violation. B
(and C) beat A on field extraction, exact match, plan feasibility and hard violations. Clarification, silent
assumptions and invented constraints show no significant A-B difference.

### B vs C

B and C have the same parsed request on all 100 items and the same recommended stops on all 100. Their statuses
differ on 47 items only because C asks a clarification question (`needs_clarification`) where B rejects
(`rejected_incomplete`); both count as asking. So every understanding and planning outcome is identical: 0
discordant pairs, p = 1, and the Wilcoxon tests are undefined (all differences 0). Latency is not significantly
different (11 077 vs 11 045 ms, p = 0.33). The only difference is KG evidence coverage (0 vs 1.000 over 38 items,
p = 7e-10), which differs by construction because B has no KG.

### C vs C-noKG (KG ablation)

The parsed requests, statuses and recommended stops are identical on all 100 items, so every understanding and
planning outcome is identical (p = 1, or undefined). The significant differences are:
- KG evidence coverage: 1.000 vs 0.000 over 38 items, p = 7.1e-10.
- KG-cited claim share: 0.439 vs 0.000 over 41 items, p = 4.7e-8.
- Latency: C is about 360 ms slower per item (11 045 vs 10 687 ms), p = 1.4e-9.

Claim support is 1.000 for both. **On this benchmark the KG changes what the explanation can cite, not which
plan is chosen**, because the evidence credit in `weighted+evidence` changed no top-ranked itinerary.

## 2. Config A's hard-violation rate (0.310 = 13 / 42 itineraries)

DEV was 0 / 28 (`results/20261128T063519Z`). On TEST, 13 of A's 42 returned itineraries violate a hard
constraint of the gold request. They come from 12 items. B, C and C-noKG have 0 / 40.

**Verifier disagreements are 0 for every config.** For each of these 13 itineraries, the planner's verifier and the
oracle agree on the request the planner saw. The violations are entirely parser errors: a constraint the driver
stated was dropped, so the planner never enforced it. The report's `violations_by_code` (access 1, arrival_soc
5, connector 4, detour 4, power 1) counts violations, not itineraries: one itinerary can carry a detour
violation per stop.

| Cause (rules parser) | Itineraries | Items | Driver's wording | Why the rule misses it |
|---|---|---|---|---|
| **Arrival-SoC floor dropped** (`arrival_soc`) | 5 | aarya-002, ian-006, manas-006, manas-013, manas-028 | "want at least 25% when I get there"; "want 25% when I get there"; "want 30% left when I get up there"; "I want 25% left when I get there"; "we want at least 25% when we arrive" | `_ARRIVAL_PATTERNS` only accepts "arriv… with N%" and "at least N% when **i** arrive / on arrival": "get there", "left" and "we arrive" are not covered |
| **Connector restriction dropped** (`connector`) | 4 | aarya-010, hriday-010, hriday-014, hriday-017 | "CCS chargers only"; "I only want CCS charging"; "only use CHAdeMO chargers"; "use CCS chargers" | `_CONNECTOR_RESTRICTION` (#178) needs the literal order "use only <connector>" |
| **Zero-detour limit dropped** (`detour`) | 3 | aarya-024 (both alternatives), manas-024 | "No detours at all"; "Zero detours" | `_DETOUR` needs a number of minutes |
| **Wheelchair restroom made optional** (`access`) | (1, one of aarya-024's two alternatives, already counted under detour) | aarya-024 | "I need … a wheelchair accessible restroom at the stop" | parsed with `wheelchair_required=True` but priority `nice_to_have` (gold: `important`), so it was not enforced, and one alternative has no accessible restroom; the "sit-down meal" was also missed |
| **Minimum power dropped** (`power`) | 1 | manas-012 | "I only want 350 kW chargers" | the rules map only "fast charg" / "dc fast" to 50 kW; there is no "N kW" rule |

There are no misread start-SoC values: A's `start_soc_pct` F1 is 1.000, the same as B's. Comma-split place names
cause no violation either. They do drive A's and B's clarification false positives (section 3). Both of
aarya-024's alternatives break the zero-detour limit, and one of them also lacks the accessible restroom, so the
13 itineraries are 5 + 4 + 3 + 1.

In total, A drops a stated hard constraint on 40 items (`parse_changed_constraints` findings), 42 values in all:
22 arrival floors, 9 minimum powers, 6 connector restrictions and 5 detour limits, where a missed limit of 0
counts as dropped. Only the 12 items above returned a plan that broke one. On the others, a plan happened to satisfy the
constraint anyway, or A rejected or failed the item for another reason. A also invents one constraint: in ian-013,
"ccs fast chargers only" became `min_power_kw = 50` and lost the CCS restriction. A rejected that item for its
places, so no itinerary was affected.

DEV did not show this because DEV's wording was what the rules were developed against (#177, #178). TEST's
phrasings for the same constraints are more varied. **This is the expected generalization gap of a hand-written
rules parser, and it is the main planning-quality argument for B/C.**

## 3. Clarification F1: why B's DEV advantage disappeared

| | n | Gold-incomplete | A: TP / FP / FN | A F1 | B: TP / FP / FN | B F1 |
|---|---|---|---|---|---|---|
| DEV (A `063519Z`, B `035410Z`) | 50 | 8 | 8 / 18 / 0 | 0.471 | 7 / 5 / 1 | **0.700** |
| TEST | 100 | 12 | 12 / 41 / 0 | 0.369 | 11 / 36 / 1 | 0.373 |

Recall stays near 1 for both configs (B's single miss is the silent assumption in section 4). **What changed is
B's false positives: 5 on DEV, 36 on TEST.** These are complete requests that B rejects as incomplete. 34 of
them are also false positives for A. A has 7 false positives that B doesn't share: aarya-009, hriday-015,
hriday-025, hriday-027, manas-016, manas-023 and manas-030. B has 2 that A doesn't share: manas-012 and manas-014.
On the per-item clarification decision, McNemar gives b = 3, c = 7, p = 0.34.

Every one of B's 36 false positives is a place that the LLM extracted but **the closed offline gazetteer
(`DataStore.geocode`, ADR 0014) could not resolve**. Vehicle and start SoC are never the cause. The surface
forms involved:

| Cause | B items (with overlap) | Examples |
|---|---|---|
| State without a comma: `place_key` normalizes "Ely, Nevada" → "ely, nv", but leaves "kingman az" unchanged, so no gazetteer key matches | 21 | ian-008 "kingman az" → "needles ca"; ian-015 "albuquerque nm" → "oklahoma city ok"; manas-015 "Washington DC" |
| Alias, landmark or district not in the gazetteer under that name (the gazetteer holds the gold form, e.g. "Pittsburgh International Airport, Pittsburgh, PA") | 14 | aarya-004 "Chicago Loop" / "O'Hare airport"; aarya-018 "LA"; manas-002 "jfk"; manas-005, manas-027, manas-029 airports; ian-004 "the medical center" |
| Extra words kept in the place | 4 | aarya-013 "Terlingua for Big Bend"; manas-010 "Grand Marais along the North Shore"; manas-012 "Sheridan, Wyoming over the Bighorns"; manas-014 "Havre on US-87" |

A's false positives have the same causes, plus 15 items where the rules' origin/destination pattern matched
nothing at all ("not extracted"). Examples are ian-001 "…, downtown phoenix az to sky harbor terminal 4" and
hriday-027. It also picks up leading words: hriday-015 "plan phoenix", manas-023 "i need" → "be".

**22 of B's 36 false positives are `ian-*` items** (22 of those 30 items). That set is lower-case, written
without commas, and uses many districts and landmarks (see section 5). DEV had no comparable set: the parser
owner's items are DEV-only, and the DEV items mostly use the "City, ST" form. So on TEST the clarification metric
mostly measures how well the gazetteer covers surface forms, not the clarification decision itself. Without the
`ian-*` items, B still has 14 false positives (airports, districts, extra words), and clarification F1 is A 0.457
vs B 0.483.

## 4. B/C silent assumptions (0.083 = 1 / 12)

One item, the same for B, C and C-noKG:

- **manas-020** (incomplete; gold: `start_soc_pct` missing). The request reads: "Taking my 2024 F-150
  Lightning Extended Range from Tulsa to Oklahoma City, **battery's at maybe half**, and I want to stop for food."
  The LLM parser set `start_soc_pct = 50`, and the request was planned (status `ok`). C did not ask either. A
  rejected the item: there is no percentage in the text, so `start_soc_pct` was missing.

This is the same pattern as DEV's hriday-022 ("half a battery" → 50 %). The other 11 gold-incomplete items are
asked about or rejected by every config. The A-B difference is a single discordant pair (p = 1).

## 5. Breakdowns

### By category (A vs B; C and C-noKG equal B in every cell)

The understanding columns come from `breakdown.csv`. Plan feasibility (valid plans / oracle-feasible items) and
hard violations (violating / returned itineraries) are counted from the per-item planning outcomes.

| Category | n | Field F1 A / B | EM A / B | Feasibility A / B | Hard violations A / B |
|---|---|---|---|---|---|
| accessibility | 10 | 0.873 / 0.915 | 0.400 / 0.600 | 3/8 / 5/8 | 1/5 / 0/7 |
| conflicting_prefs | 11 | 0.825 / 0.921 | 0.091 / 0.636 | 3/5 / 4/5 | 3/6 / 0/4 |
| incomplete | 12 | 0.907 / 0.988 | 0.667 / 0.917 | n/a (not evaluable) | n/a |
| multi_stop | 13 | 0.882 / 0.985 | 0.538 / 0.923 | 5/13 / 8/13 | 1/7 / 0/8 |
| priority_tradeoff | 6 | 0.839 / 0.925 | 0.167 / 0.667 | 2/5 / 3/5 | 0/2 / 0/3 |
| rural | 18 | 0.910 / 0.958 | 0.444 / 0.778 | 8/17 / 13/17 | 4/12 / 0/13 |
| sparse_charging | 13 | 0.874 / 0.954 | 0.385 / 0.769 | 4/8 / 4/8 | 3/7 / 0/4 |
| urban | 17 | 0.802 / 0.856 | 0.235 / 0.471 | 2/14 / 3/14 | 1/3 / 0/3 |

Urban is the weakest category for both configs: B plans only 3 of 14 oracle-feasible urban items, because airports
and districts don't resolve (section 3). A's largest exact-match gap is conflicting preferences (0.091 vs 0.636),
where "no detours at all" / "zero detours" are lost.

### By author

| Author | n | Field F1 A / B | EM A / B | Clar. F1 A / B | Feasibility A / B | Hard violations A / B |
|---|---|---|---|---|---|---|
| aarya | 15 | 0.827 / 0.901 | 0.333 / 0.600 | 0.333 / 0.400 | 5/11 / 8/11 | 4/9 / 0/8 |
| hriday | 26 | 0.898 / 0.980 | 0.385 / 0.885 | 0.500 / 0.667 | 10/18 / 14/18 | 3/15 / 0/16 |
| manas | 29 | 0.860 / 0.915 | 0.379 / 0.586 | 0.471 / 0.400 | 9/21 / 14/21 | 5/14 / 0/14 |
| **ian** (AI-drafted) | 30 | 0.852 / 0.925 | 0.400 / 0.767 | 0.267 / 0.267 | **3/20 / 4/20** | 1/4 / 0/4 |

### Sensitivity check: the AI-drafted `ian-*` items

The 30 `ian-*` test items were drafted with an AI assistant, not written by hand. Below is the headline
recomputed without them (70 items) and on them alone:

| Subset | Config | Field F1 | EM | EM strict | Clar. F1 (TP/FP/FN) | Silent | Feasibility | Hard violations |
|---|---|---|---|---|---|---|---|---|
| without ian (70) | A | 0.868 | 0.371 | 0.057 | 0.457 (8/19/0) | 0/8 | 24/50 = 0.480 | 12/38 = 0.316 |
| without ian (70) | B = C = C-noKG | 0.937 | 0.700 | 0.057 | 0.483 (7/14/1) | 1/8 | 36/50 = 0.720 | 0/38 |
| ian only (30) | A | 0.852 | 0.400 | 0.400 | 0.267 (4/22/0) | 0/4 | 3/20 = 0.150 | 1/4 |
| ian only (30) | B = C = C-noKG | 0.925 | 0.767 | 0.767 | 0.267 (4/22/0) | 0/4 | 4/20 = 0.200 | 0/4 |

Paired A vs B tests without the `ian-*` items (n = 70):
- Significant: exact match (b = 2, c = 25, p = 2.3e-5), field F1 (p = 3.3e-6), strict field F1 (p = 1.8e-5),
  got a valid plan (b = 1, c = 13, p = 0.0018), no hard violation (b = 0, c = 7, p = 0.016).
- Not significant: clarification decision (p = 0.34).
- Strict exact match becomes equal (0.057 vs 0.057). Its significance on the full set comes from the `ian-*`
  items only.
- B vs C and C vs C-noKG are unchanged: identical except the KG metrics and latency.

**Conclusion: every A-vs-B conclusion holds without the AI-drafted items.** Their main effect is on plan
feasibility: they lower B's from 0.720 to 0.571 overall, because 22 of 30 are rejected for unresolved places. They
also flatten the clarification comparison. Report the 70-item numbers next to the headline.

## 6. Oracle cutoffs on TEST (label limit)

The oracle's Pareto search stopped at `max_labels` (40 000) on **12 of the 40 items searched for B, C and
C-noKG**, and on 10 of 35 for A. A planned neither hriday-015 nor manas-016, so their fronts were never searched.
Every cutoff is `max_labels`. None hit the 600 s time budget (`items_nondeterministic_cutoff` is empty), so the
excluded set is reproducible.

| Item | Category | Complete plans found before the cutoff | In A's cutoffs |
|---|---|---|---|
| aarya-023 | conflicting_prefs | 4 | yes |
| hriday-015 | multi_stop | 0 | no (A rejected the item) |
| hriday-016 | multi_stop | 0 | yes |
| hriday-017 | multi_stop | 4 | yes |
| hriday-018 | multi_stop | 0 | yes |
| hriday-029 | priority_tradeoff | 0 | yes |
| hriday-030 | priority_tradeoff | 0 | yes |
| ian-018 | multi_stop | 0 | yes |
| manas-016 | multi_stop | 0 | no (A rejected the item) |
| manas-017 | multi_stop | 3 | yes |
| manas-018 | multi_stop | 3 | yes |
| manas-028 | accessibility | 1 | yes |

8 of the 12 are multi-stop trips (8 of the 13 multi-stop items), i.e. long corridors with many stations. These
items are left out of the **front-based** metrics only: Pareto membership, robust plan rate, unknown-power and
unknown-status sensitivity. Plan feasibility and the hard-violation rate still judge them, because they use the
oracle's separate feasibility check and `is_valid`. B, C and C-noKG returned a valid plan on all 12. A has
violations on two of them (hriday-017 connector, manas-028 arrival SoC). DEV had 8 cutoffs out of 36 searched
(B) and 5 out of 20 (A).

## Summary

- **A vs B/C:** B/C are significantly better on field F1, exact match (both levels), plan feasibility (0.571 vs
  0.386) and hard violations (0 vs 0.310). These results survive Holm correction and dropping the AI-drafted items.
  There is no significant difference in clarification, silent assumptions or invented constraints.
- **B vs C and C vs C-noKG:** identical on every understanding and planning outcome. The KG adds cited evidence
  (coverage 1.0, KG-cited claim share 0.49) and about 0.36 s per item, and changes no recommended plan.
- **A's 0.310:** 13 itineraries from 12 items, all dropped constraints, with 0 verifier disagreements. Causes:
  arrival floor 5, connector 4, zero detour 3, minimum power 1, plus one access violation in aarya-024.
- **Clarification F1:** both configs are at about 0.37 because of 36–41 false rejections of complete requests.
  All of them are places the closed gazetteer cannot resolve (state without a comma, airports and landmarks,
  extra words). 22 are `ian-*` items.
- **Silent assumption:** manas-020, "maybe half" → 50 %.
- **Oracle cutoffs:** 12 items (8 multi-stop), all at `max_labels` and reproducible, excluded from front-based
  metrics only.
