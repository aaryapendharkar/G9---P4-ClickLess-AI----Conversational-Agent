# Evaluation report: 20261128T100314Z_recomputed

Split: test -- FINAL (held-out test)
Configs: A, B, C, C-noKG. Bootstrap: 10000 resamples, seed 573, percentile 95 % CI.

## Headline

Value [95 % CI]. n/a = metric block missing or not implemented; [CI n/a] = the metric block reports no CI.
Field F1 and exact match compare places at city level; (strict) also requires the gold's state, which annotators add for geocoding. Parse / planning error rates are over all items.
Hard-violation rate (vs gold): measured against the gold request (includes parser errors); verifier disagreements: A 0, B 0, C 0, C-noKG 0 (findings where the planner's verifier and the oracle, on the request the verifier saw, disagree).
Plan feasibility and hard-violation CIs resample items: A from result JSON; B from result JSON; C from result JSON; C-noKG from result JSON.
KG evidence coverage: share of the recommended plan's displayed stop facts (station name, connector, power, amenities) supported by the result's own KG evidence, mean over items with a charging stop. KG-cited claim share: claims citing at least one KG fact, over all claims.

| Config | n | Field F1 (micro) | Field F1 (strict) | Exact match | Exact match (strict) | Clarification F1 | Silent-assumption rate | Invented-constraint rate | Parse error rate | Planning error rate | Plan feasibility | Hard-violation rate (vs gold) | Grounding (claim support) | KG evidence coverage | KG-cited claim share | Freshness mislabel rate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | 100 | 0.863 [0.836, 0.890] | 0.682 [0.645, 0.719] | 0.380 [0.290, 0.480] | 0.160 [0.090, 0.230] | 0.369 [0.208, 0.514] | 0.000 [0.000, 0.000] | 0.010 [0.000, 0.030] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.386 [0.271, 0.500] | 0.310 [0.167, 0.463] | n/a | 0.000 [0.000, 0.000] | n/a | n/a |
| B | 100 | 0.934 [0.910, 0.956] | 0.745 [0.713, 0.779] | 0.720 [0.630, 0.810] | 0.270 [0.190, 0.360] | 0.373 [0.204, 0.523] | 0.083 [0.000, 0.273] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.571 [0.457, 0.686] | 0.000 [0.000, 0.000] | n/a | 0.000 [0.000, 0.000] | n/a | n/a |
| C | 100 | 0.934 [0.910, 0.956] | 0.745 [0.713, 0.779] | 0.720 [0.630, 0.810] | 0.270 [0.190, 0.360] | 0.373 [0.204, 0.523] | 0.083 [0.000, 0.273] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.571 [0.457, 0.686] | 0.000 [0.000, 0.000] | 1.000 [CI n/a] | 1.000 [1.000, 1.000] | 0.494 [0.436, 0.548] | 0.000 [CI n/a] |
| C-noKG | 100 | 0.934 [0.910, 0.956] | 0.745 [0.713, 0.779] | 0.720 [0.630, 0.810] | 0.270 [0.190, 0.360] | 0.373 [0.204, 0.523] | 0.083 [0.000, 0.273] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.571 [0.457, 0.686] | 0.000 [0.000, 0.000] | 1.000 [CI n/a] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [CI n/a] |

## LLM parser fallback

Items an LLM parser handled, and how many it handed to the rules fallback (timeout, unreachable endpoint, or two invalid responses). Reasons are the parser's logged warnings.

| Config | Parser | LLM-parsed | Fallbacks | Fallback rate | Reasons |
|---|---|---|---|---|---|
| A | rules | 0 | 0 | n/a | no LLM parser |
| B | llm | 100 | 0 | 0.000 | - |
| C | llm | 100 | 0 | 0.000 | - |
| C-noKG | llm | 100 | 0 | 0.000 | - |

## Paired comparisons

McNemar on per-item successes (b = first config only, c = second only); Wilcoxon signed-rank on per-item scores. Items both configs ran.

| Pair | Outcome | Test | n | First | Second | b | c | Statistic | p |
|---|---|---|---|---|---|---|---|---|---|
| A vs B | exact_match | McNemar | 100 | 0.380 | 0.720 | 3 | 37 | 27.225 | 0.0000 |
| A vs B | clarification_correct | McNemar | 100 | 0.590 | 0.630 | 3 | 7 | 3.000 | 0.3438 |
| A vs B | no_error | McNemar | 100 | 1.000 | 1.000 | 0 | 0 | 0.000 | 1.0000 |
| A vs B | no_invented_constraint | McNemar | 100 | 0.990 | 1.000 | 0 | 1 | 0.000 | 1.0000 |
| A vs B | got_valid_plan | McNemar | 70 | 0.386 | 0.571 | 1 | 14 | 1.000 | 0.0010 |
| A vs B | field_f1 | Wilcoxon | 100 | 0.856 | 0.936 | n/a | n/a | 231.500 | 0.0000 |
| A vs B | latency_ms | Wilcoxon | 100 | 864.765 | 11077.499 | n/a | n/a | 0.000 | 0.0000 |
| A vs C | exact_match | McNemar | 100 | 0.380 | 0.720 | 3 | 37 | 27.225 | 0.0000 |
| A vs C | clarification_correct | McNemar | 100 | 0.590 | 0.630 | 3 | 7 | 3.000 | 0.3438 |
| A vs C | no_error | McNemar | 100 | 1.000 | 1.000 | 0 | 0 | 0.000 | 1.0000 |
| A vs C | no_invented_constraint | McNemar | 100 | 0.990 | 1.000 | 0 | 1 | 0.000 | 1.0000 |
| A vs C | got_valid_plan | McNemar | 70 | 0.386 | 0.571 | 1 | 14 | 1.000 | 0.0010 |
| A vs C | field_f1 | Wilcoxon | 100 | 0.856 | 0.936 | n/a | n/a | 231.500 | 0.0000 |
| A vs C | latency_ms | Wilcoxon | 100 | 864.765 | 11044.820 | n/a | n/a | 0.000 | 0.0000 |
| B vs C | exact_match | McNemar | 100 | 0.720 | 0.720 | 0 | 0 | 0.000 | 1.0000 |
| B vs C | clarification_correct | McNemar | 100 | 0.630 | 0.630 | 0 | 0 | 0.000 | 1.0000 |
| B vs C | no_error | McNemar | 100 | 1.000 | 1.000 | 0 | 0 | 0.000 | 1.0000 |
| B vs C | no_invented_constraint | McNemar | 100 | 1.000 | 1.000 | 0 | 0 | 0.000 | 1.0000 |
| B vs C | got_valid_plan | McNemar | 70 | 0.571 | 0.571 | 0 | 0 | 0.000 | 1.0000 |
| B vs C | field_f1 | Wilcoxon | 100 | 0.936 | 0.936 | n/a | n/a | n/a | n/a |
| B vs C | latency_ms | Wilcoxon | 100 | 11077.499 | 11044.820 | n/a | n/a | 2243.000 | 0.3322 |
| C vs C-noKG | exact_match | McNemar | 100 | 0.720 | 0.720 | 0 | 0 | 0.000 | 1.0000 |
| C vs C-noKG | clarification_correct | McNemar | 100 | 0.630 | 0.630 | 0 | 0 | 0.000 | 1.0000 |
| C vs C-noKG | no_error | McNemar | 100 | 1.000 | 1.000 | 0 | 0 | 0.000 | 1.0000 |
| C vs C-noKG | no_invented_constraint | McNemar | 100 | 1.000 | 1.000 | 0 | 0 | 0.000 | 1.0000 |
| C vs C-noKG | got_valid_plan | McNemar | 70 | 0.571 | 0.571 | 0 | 0 | 0.000 | 1.0000 |
| C vs C-noKG | field_f1 | Wilcoxon | 100 | 0.936 | 0.936 | n/a | n/a | n/a | n/a |
| C vs C-noKG | latency_ms | Wilcoxon | 100 | 11044.820 | 10686.524 | n/a | n/a | 764.000 | 0.0000 |
| C vs C-noKG | kg_evidence_coverage | Wilcoxon | 38 | 1.000 | 0.000 | n/a | n/a | 0.000 | 0.0000 |
| C vs C-noKG | kg_claim_share | Wilcoxon | 41 | 0.439 | 0.000 | n/a | n/a | 0.000 | 0.0000 |

## Handoff correctness (offline, #185)

For each ok item the harness approved a COPY of the result for the recommended option (no real approval, no navigation) and saved the Google Maps URLs and the expected stops. They are checked by evaluation.metrics.handoff, which parses the URLs and does not use the handoff builder. URL checks: parses, stops in order, each stop once, segment boundaries, no extra stops. Matches plan: the approved stops are the saved recommended itinerary's. Endpoints: the URLs start at the request's origin and end at its destination. Approved: exactly one approval entry in the run folder's audit log. Run-wide: no saved result is approved or carries URLs, and no item without status ok has a handoff.

| Config | ok items | Scored | URL checks | Matches plan | Endpoints | Approved (audit) | Fully correct | Saved results approved | Non-ok items with a handoff | Handoff errors |
|---|---|---|---|---|---|---|---|---|---|---|
| A | 39 | 39 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 (39/39) | 0/100 | 0/61 | 0 |
| B | 41 | 41 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 (41/41) | 0/100 | 0/59 | 0 |
| C | 41 | 41 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 (41/41) | 0/100 | 0/59 | 0 |
| C-noKG | 41 | 41 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 (41/41) | 0/100 | 0/59 | 0 |

## Latency (#185)

Measured on: one Windows laptop (RTX 2060 6 GB; qwen3:8b ~70% on GPU, rest on CPU). LLM GPU share (median): B 0.703, C 0.703, C-noKG 0.703. Latency depends on the machine; compare configs within this run only.

Request latency (`latency_ms`) runs from just before pipeline.run() until it returns or raises: parse, route, retrieve, plan+verify, rank, explain. It excludes the one-off LLM warm-up before the first item and the offline handoff check. All rows count, errors included (their count is shown); routed items are those that got past parsing to routing (rejected requests stop after parsing). Stage laps come from each result's timings_ms; an error row keeps the laps recorded before the exception. p50 / p95: numpy linear interpolation.

| Config | Scope | n | p50 (ms) | p95 (ms) | Mean (ms) | Max (ms) | Error rows |
|---|---|---|---|---|---|---|---|
| A | request (all rows) | 100 | 1 | 3,919 | 865 | 21,434 | 0 |
| A | request (non-error rows) | 100 | 1 | 3,919 | 865 | 21,434 | 0 |
| A | request (routed items) | 47 | 215 | 7,353 | 1,839 | 21,434 | 0 |
| A | stage: parse | 100 | 1 | 1 | 1 | 8 | 0 |
| A | stage: route | 47 | 4 | 17 | 5 | 20 | 0 |
| A | stage: retrieve | 47 | 81 | 1,216 | 329 | 1,984 | 0 |
| A | stage: plan+verify | 47 | 47 | 6,175 | 1,445 | 21,076 | 0 |
| A | stage: rank | 39 | 0 | 133 | 40 | 952 | 0 |
| A | stage: explain | 100 | 0 | 0 | 0 | 27 | 0 |
| B | request (all rows) | 100 | 9,976 | 18,860 | 11,077 | 39,300 | 0 |
| B | request (non-error rows) | 100 | 9,976 | 18,860 | 11,077 | 39,300 | 0 |
| B | request (routed items) | 53 | 10,758 | 27,290 | 13,183 | 39,300 | 0 |
| B | stage: parse | 100 | 9,597 | 14,901 | 9,633 | 18,584 | 0 |
| B | stage: route | 53 | 9 | 33 | 27 | 806 | 0 |
| B | stage: retrieve | 53 | 183 | 2,052 | 973 | 28,540 | 0 |
| B | stage: plan+verify | 53 | 97 | 6,255 | 1,647 | 25,033 | 0 |
| B | stage: rank | 41 | 1 | 203 | 56 | 1,335 | 0 |
| B | stage: explain | 100 | 0 | 0 | 0 | 30 | 0 |
| C | request (all rows) | 100 | 10,056 | 19,429 | 11,045 | 47,284 | 0 |
| C | request (non-error rows) | 100 | 10,056 | 19,429 | 11,045 | 47,284 | 0 |
| C | request (routed items) | 53 | 10,758 | 21,614 | 13,266 | 47,284 | 0 |
| C | stage: parse | 100 | 9,376 | 14,693 | 9,503 | 18,979 | 0 |
| C | stage: route | 53 | 9 | 27 | 11 | 43 | 0 |
| C | stage: retrieve | 53 | 413 | 2,601 | 869 | 10,083 | 0 |
| C | stage: plan+verify | 53 | 72 | 5,902 | 1,614 | 23,207 | 0 |
| C | stage: rank | 41 | 3 | 733 | 452 | 13,386 | 0 |
| C | stage: explain | 100 | 0 | 0 | 0 | 27 | 0 |
| C-noKG | request (all rows) | 100 | 9,852 | 18,981 | 10,687 | 35,308 | 0 |
| C-noKG | request (non-error rows) | 100 | 9,852 | 18,981 | 10,687 | 35,308 | 0 |
| C-noKG | request (routed items) | 53 | 10,435 | 21,017 | 12,656 | 35,308 | 0 |
| C-noKG | stage: parse | 100 | 9,228 | 14,715 | 9,448 | 18,472 | 0 |
| C-noKG | stage: route | 53 | 10 | 28 | 26 | 818 | 0 |
| C-noKG | stage: retrieve | 53 | 230 | 1,793 | 607 | 8,932 | 0 |
| C-noKG | stage: plan+verify | 53 | 87 | 6,089 | 1,625 | 23,049 | 0 |
| C-noKG | stage: rank | 41 | 1 | 181 | 54 | 1,318 | 0 |
| C-noKG | stage: explain | 100 | 0 | 0 | 0 | 27 | 0 |

## Provenance completeness (#186)

Over the displayed charging-stop facts of each recommended plan (station name, connector, power, amenities: the facts KG evidence coverage counts), pooled over items. A fact is complete when a fact in the result's own KG evidence supports it and that fact's provenance has source, source_record_id and retrieved_at; a displayed fact without supporting evidence is never complete (missing: evidence). Validate-required counts only source and retrieved_at, the fields clickless.data.validate requires of every record. source_updated_at is optional there (OSM does not publish it) and is reported as a share. CI: bootstrap over items.

| Config | Items with stops | Displayed facts | With evidence | Complete | Validate-required | source_updated_at | Missing: evidence / source / record id / retrieved_at / updated_at | Note |
|---|---|---|---|---|---|---|---|---|
| A | 35 | 257 | 0 | 0.000 [0.000, 0.000] | 0.000 | 0.000 | 257 / 0 / 0 / 0 / 0 | no evidence in any result (by design: no KG) |
| B | 38 | 314 | 0 | 0.000 [0.000, 0.000] | 0.000 | 0.000 | 314 / 0 / 0 / 0 / 0 | no evidence in any result (by design: no KG) |
| C | 38 | 314 | 314 | 1.000 [1.000, 1.000] | 1.000 | 0.564 | 0 / 0 / 0 / 0 / 137 | - |
| C-noKG | 38 | 314 | 0 | 0.000 [0.000, 0.000] | 0.000 | 0.000 | 314 / 0 / 0 / 0 / 0 | no evidence in any result (by design: no KG) |

## Errors by stage and type

No errors.

## Breakdowns

### By author

| Config | author | n | Parse err | Planning err | Field F1 | F1 (strict) | Exact match | EM (strict) | Clarification F1 | Silent-assumption | Invented-constraint |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A | aarya | 15 | 0.000 | 0.000 | 0.827 | 0.533 | 0.333 | 0.067 | 0.333 | 0.000 | 0.000 |
| A | hriday | 26 | 0.000 | 0.000 | 0.898 | 0.642 | 0.385 | 0.038 | 0.500 | 0.000 | 0.000 |
| A | ian | 30 | 0.000 | 0.000 | 0.852 | 0.852 | 0.400 | 0.400 | 0.267 | 0.000 | 0.033 |
| A | manas | 29 | 0.000 | 0.000 | 0.860 | 0.645 | 0.379 | 0.069 | 0.471 | 0.000 | 0.000 |
| B | aarya | 15 | 0.000 | 0.000 | 0.901 | 0.605 | 0.600 | 0.067 | 0.400 | 0.000 | 0.000 |
| B | hriday | 26 | 0.000 | 0.000 | 0.980 | 0.694 | 0.885 | 0.038 | 0.667 | 0.000 | 0.000 |
| B | ian | 30 | 0.000 | 0.000 | 0.925 | 0.925 | 0.767 | 0.767 | 0.267 | 0.000 | 0.000 |
| B | manas | 29 | 0.000 | 0.000 | 0.915 | 0.696 | 0.586 | 0.069 | 0.400 | 0.250 | 0.000 |
| C | aarya | 15 | 0.000 | 0.000 | 0.901 | 0.605 | 0.600 | 0.067 | 0.400 | 0.000 | 0.000 |
| C | hriday | 26 | 0.000 | 0.000 | 0.980 | 0.694 | 0.885 | 0.038 | 0.667 | 0.000 | 0.000 |
| C | ian | 30 | 0.000 | 0.000 | 0.925 | 0.925 | 0.767 | 0.767 | 0.267 | 0.000 | 0.000 |
| C | manas | 29 | 0.000 | 0.000 | 0.915 | 0.696 | 0.586 | 0.069 | 0.400 | 0.250 | 0.000 |
| C-noKG | aarya | 15 | 0.000 | 0.000 | 0.901 | 0.605 | 0.600 | 0.067 | 0.400 | 0.000 | 0.000 |
| C-noKG | hriday | 26 | 0.000 | 0.000 | 0.980 | 0.694 | 0.885 | 0.038 | 0.667 | 0.000 | 0.000 |
| C-noKG | ian | 30 | 0.000 | 0.000 | 0.925 | 0.925 | 0.767 | 0.767 | 0.267 | 0.000 | 0.000 |
| C-noKG | manas | 29 | 0.000 | 0.000 | 0.915 | 0.696 | 0.586 | 0.069 | 0.400 | 0.250 | 0.000 |

### By category

| Config | category | n | Parse err | Planning err | Field F1 | F1 (strict) | Exact match | EM (strict) | Clarification F1 | Silent-assumption | Invented-constraint |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A | accessibility | 10 | 0.000 | 0.000 | 0.873 | 0.709 | 0.400 | 0.100 | 0.000 | n/a | 0.000 |
| A | conflicting_prefs | 11 | 0.000 | 0.000 | 0.825 | 0.619 | 0.091 | 0.000 | 0.000 | n/a | 0.000 |
| A | incomplete | 12 | 0.000 | 0.000 | 0.907 | 0.720 | 0.667 | 0.333 | 1.000 | 0.000 | 0.000 |
| A | multi_stop | 13 | 0.000 | 0.000 | 0.882 | 0.677 | 0.538 | 0.231 | 0.000 | n/a | 0.000 |
| A | priority_tradeoff | 6 | 0.000 | 0.000 | 0.839 | 0.645 | 0.167 | 0.000 | 0.000 | n/a | 0.000 |
| A | rural | 18 | 0.000 | 0.000 | 0.910 | 0.742 | 0.444 | 0.167 | 0.000 | n/a | 0.000 |
| A | sparse_charging | 13 | 0.000 | 0.000 | 0.874 | 0.706 | 0.385 | 0.308 | 0.000 | n/a | 0.077 |
| A | urban | 17 | 0.000 | 0.000 | 0.802 | 0.630 | 0.235 | 0.059 | 0.000 | n/a | 0.000 |
| B | accessibility | 10 | 0.000 | 0.000 | 0.915 | 0.746 | 0.600 | 0.200 | 0.000 | n/a | 0.000 |
| B | conflicting_prefs | 11 | 0.000 | 0.000 | 0.921 | 0.691 | 0.636 | 0.091 | 0.000 | n/a | 0.000 |
| B | incomplete | 12 | 0.000 | 0.000 | 0.988 | 0.741 | 0.917 | 0.417 | 0.957 | 0.083 | 0.000 |
| B | multi_stop | 13 | 0.000 | 0.000 | 0.985 | 0.765 | 0.923 | 0.308 | 0.000 | n/a | 0.000 |
| B | priority_tradeoff | 6 | 0.000 | 0.000 | 0.925 | 0.746 | 0.667 | 0.333 | 0.000 | n/a | 0.000 |
| B | rural | 18 | 0.000 | 0.000 | 0.958 | 0.768 | 0.778 | 0.222 | 0.000 | n/a | 0.000 |
| B | sparse_charging | 13 | 0.000 | 0.000 | 0.954 | 0.831 | 0.769 | 0.538 | 0.000 | n/a | 0.000 |
| B | urban | 17 | 0.000 | 0.000 | 0.856 | 0.689 | 0.471 | 0.118 | 0.000 | n/a | 0.000 |
| C | accessibility | 10 | 0.000 | 0.000 | 0.915 | 0.746 | 0.600 | 0.200 | 0.000 | n/a | 0.000 |
| C | conflicting_prefs | 11 | 0.000 | 0.000 | 0.921 | 0.691 | 0.636 | 0.091 | 0.000 | n/a | 0.000 |
| C | incomplete | 12 | 0.000 | 0.000 | 0.988 | 0.741 | 0.917 | 0.417 | 0.957 | 0.083 | 0.000 |
| C | multi_stop | 13 | 0.000 | 0.000 | 0.985 | 0.765 | 0.923 | 0.308 | 0.000 | n/a | 0.000 |
| C | priority_tradeoff | 6 | 0.000 | 0.000 | 0.925 | 0.746 | 0.667 | 0.333 | 0.000 | n/a | 0.000 |
| C | rural | 18 | 0.000 | 0.000 | 0.958 | 0.768 | 0.778 | 0.222 | 0.000 | n/a | 0.000 |
| C | sparse_charging | 13 | 0.000 | 0.000 | 0.954 | 0.831 | 0.769 | 0.538 | 0.000 | n/a | 0.000 |
| C | urban | 17 | 0.000 | 0.000 | 0.856 | 0.689 | 0.471 | 0.118 | 0.000 | n/a | 0.000 |
| C-noKG | accessibility | 10 | 0.000 | 0.000 | 0.915 | 0.746 | 0.600 | 0.200 | 0.000 | n/a | 0.000 |
| C-noKG | conflicting_prefs | 11 | 0.000 | 0.000 | 0.921 | 0.691 | 0.636 | 0.091 | 0.000 | n/a | 0.000 |
| C-noKG | incomplete | 12 | 0.000 | 0.000 | 0.988 | 0.741 | 0.917 | 0.417 | 0.957 | 0.083 | 0.000 |
| C-noKG | multi_stop | 13 | 0.000 | 0.000 | 0.985 | 0.765 | 0.923 | 0.308 | 0.000 | n/a | 0.000 |
| C-noKG | priority_tradeoff | 6 | 0.000 | 0.000 | 0.925 | 0.746 | 0.667 | 0.333 | 0.000 | n/a | 0.000 |
| C-noKG | rural | 18 | 0.000 | 0.000 | 0.958 | 0.768 | 0.778 | 0.222 | 0.000 | n/a | 0.000 |
| C-noKG | sparse_charging | 13 | 0.000 | 0.000 | 0.954 | 0.831 | 0.769 | 0.538 | 0.000 | n/a | 0.000 |
| C-noKG | urban | 17 | 0.000 | 0.000 | 0.856 | 0.689 | 0.471 | 0.118 | 0.000 | n/a | 0.000 |

### By route_type

| Config | route_type | n | Parse err | Planning err | Field F1 | F1 (strict) | Exact match | EM (strict) | Clarification F1 | Silent-assumption | Invented-constraint |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A | mixed | 35 | 0.000 | 0.000 | 0.873 | 0.639 | 0.457 | 0.114 | 0.621 | 0.000 | 0.000 |
| A | rural | 36 | 0.000 | 0.000 | 0.885 | 0.704 | 0.361 | 0.194 | 0.000 | n/a | 0.028 |
| A | urban | 29 | 0.000 | 0.000 | 0.824 | 0.706 | 0.310 | 0.172 | 0.240 | 0.000 | 0.000 |
| B | mixed | 35 | 0.000 | 0.000 | 0.969 | 0.708 | 0.829 | 0.200 | 0.667 | 0.111 | 0.000 |
| B | rural | 36 | 0.000 | 0.000 | 0.948 | 0.766 | 0.750 | 0.306 | 0.000 | n/a | 0.000 |
| B | urban | 29 | 0.000 | 0.000 | 0.875 | 0.763 | 0.552 | 0.310 | 0.240 | 0.000 | 0.000 |
| C | mixed | 35 | 0.000 | 0.000 | 0.969 | 0.708 | 0.829 | 0.200 | 0.667 | 0.111 | 0.000 |
| C | rural | 36 | 0.000 | 0.000 | 0.948 | 0.766 | 0.750 | 0.306 | 0.000 | n/a | 0.000 |
| C | urban | 29 | 0.000 | 0.000 | 0.875 | 0.763 | 0.552 | 0.310 | 0.240 | 0.000 | 0.000 |
| C-noKG | mixed | 35 | 0.000 | 0.000 | 0.969 | 0.708 | 0.829 | 0.200 | 0.667 | 0.111 | 0.000 |
| C-noKG | rural | 36 | 0.000 | 0.000 | 0.948 | 0.766 | 0.750 | 0.306 | 0.000 | n/a | 0.000 |
| C-noKG | urban | 29 | 0.000 | 0.000 | 0.875 | 0.763 | 0.552 | 0.310 | 0.240 | 0.000 | 0.000 |

## Invented constraints

Items whose parsed request has a hard constraint (connector, minimum power, maximum detour, minimum arrival SoC) that the gold request does not have (#145). Separate from silent assumptions, which only cover incomplete requests.

| Config | Item | Invented | Parsed values |
|---|---|---|---|
| A | ian-013 | min_power_kw | min_power_kw=50.0 |

## Oracle search

Items whose oracle Pareto search stopped early (time budget or max_labels). They get an `oracle_search_incomplete` finding and are left out of the front-based metrics.

| Config | Items searched | Incomplete | Reasons | Budget (s) | Items |
|---|---|---|---|---|---|
| A | 35 | 10 | max_labels: 10 | 600 | aarya-023 hriday-016 hriday-017 hriday-018 hriday-029 hriday-030 ian-018 manas-017 manas-018 manas-028 |
| B | 40 | 12 | max_labels: 12 | 600 | aarya-023 hriday-015 hriday-016 hriday-017 hriday-018 hriday-029 hriday-030 ian-018 manas-016 manas-017 manas-018 manas-028 |
| C | 40 | 12 | max_labels: 12 | 600 | aarya-023 hriday-015 hriday-016 hriday-017 hriday-018 hriday-029 hriday-030 ian-018 manas-016 manas-017 manas-018 manas-028 |
| C-noKG | 40 | 12 | max_labels: 12 | 600 | aarya-023 hriday-015 hriday-016 hriday-017 hriday-018 hriday-029 hriday-030 ian-018 manas-016 manas-017 manas-018 manas-028 |

## Ontology adequacy (competency questions on the fixture KG)

Measures whether the ontology and queries can answer the CQs, not whether the snapshot data covers them.

Competency questions (CQ1-CQ20, inventory.json) the KG answers: status "tested", the query runs, and it returns rows with the inventory's example bindings (or the inventory marks the question empty on fixtures). Run once per run on the fixture KG, where those bindings are valid; only configs that build the KG (C) are scored.

| Config | Answerable | Share | Inventory version | Snapshot | Failing |
|---|---|---|---|---|---|
| A | n/a: config A does not build the KG | n/a | n/a | snapshot:96dfbabb4837 (frozen, created 2026-11-26T00:55:26.616109+00:00) | n/a |
| B | n/a: config B does not build the KG | n/a | n/a | snapshot:96dfbabb4837 (frozen, created 2026-11-26T00:55:26.616109+00:00) | n/a |
| C | 20/20 | 1.000 | 2 | snapshot:96dfbabb4837 (frozen, created 2026-11-26T00:55:26.616109+00:00) | - |
| C-noKG | n/a: config C-noKG does not build the KG | n/a | n/a | snapshot:96dfbabb4837 (frozen, created 2026-11-26T00:55:26.616109+00:00) | n/a |

## Findings (planner/oracle disagreements)

| Config | Item | Kind | Detail |
|---|---|---|---|
| A | aarya-023 | oracle_search_incomplete | max_labels (40000) hit; 4 plan(s) found; excluded from front-based metrics |
| A | hriday-016 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| A | hriday-017 | oracle_search_incomplete | max_labels (40000) hit; 4 plan(s) found; excluded from front-based metrics |
| A | hriday-018 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| A | hriday-029 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| A | hriday-030 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| A | ian-018 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| A | manas-017 | oracle_search_incomplete | max_labels (40000) hit; 3 plan(s) found; excluded from front-based metrics |
| A | manas-018 | oracle_search_incomplete | max_labels (40000) hit; 3 plan(s) found; excluded from front-based metrics |
| A | manas-028 | oracle_search_incomplete | max_labels (40000) hit; 1 plan(s) found; excluded from front-based metrics |
| A | aarya-002 | parse_changed_constraints | arrival_soc_pct_min: gold 25.0, parsed None |
| A | aarya-004 | parse_changed_constraints | min_power_kw: gold 150.0, parsed None |
| A | aarya-007 | parse_changed_constraints | arrival_soc_pct_min: gold 30.0, parsed None |
| A | aarya-009 | parse_changed_constraints | arrival_soc_pct_min: gold 15.0, parsed None |
| A | aarya-010 | parse_changed_constraints | connectors_override: gold ['CCS1'], parsed None |
| A | aarya-013 | parse_changed_constraints | arrival_soc_pct_min: gold 25.0, parsed None |
| A | aarya-018 | parse_changed_constraints | min_power_kw: gold 150.0, parsed None |
| A | aarya-024 | parse_changed_constraints | max_detour_min: gold 0.0, parsed None |
| A | aarya-025 | parse_changed_constraints | min_power_kw: gold 250.0, parsed None |
| A | hriday-002 | oracle_feasible_planner_rejected | oracle witness ['afdc:63047'] |
| A | hriday-003 | parse_changed_constraints | connectors_override: gold ['CCS1'], parsed None |
| A | hriday-004 | parse_changed_constraints | min_power_kw: gold 150.0, parsed None |
| A | hriday-005 | parse_changed_constraints | arrival_soc_pct_min: gold 20.0, parsed None |
| A | hriday-006 | parse_changed_constraints | arrival_soc_pct_min: gold 20.0, parsed None |
| A | hriday-008 | parse_changed_constraints | min_power_kw: gold 100.0, parsed None |
| A | hriday-010 | parse_changed_constraints | arrival_soc_pct_min: gold 18.0, parsed None; connectors_override: gold ['CCS1'], parsed None |
| A | hriday-014 | parse_changed_constraints | connectors_override: gold ['CHADEMO'], parsed None |
| A | hriday-015 | parse_changed_constraints | arrival_soc_pct_min: gold 20.0, parsed None |
| A | hriday-017 | parse_changed_constraints | connectors_override: gold ['CCS1'], parsed None |
| A | hriday-018 | parse_changed_constraints | arrival_soc_pct_min: gold 20.0, parsed None; min_power_kw: gold 150.0, parsed None |
| A | hriday-024 | parse_changed_constraints | max_detour_min: gold 0.0, parsed None |
| A | ian-002 | parse_changed_constraints | arrival_soc_pct_min: gold 20.0, parsed None |
| A | ian-003 | parse_changed_constraints | arrival_soc_pct_min: gold 15.0, parsed None |
| A | ian-006 | parse_changed_constraints | arrival_soc_pct_min: gold 25.0, parsed None |
| A | ian-009 | parse_changed_constraints | arrival_soc_pct_min: gold 20.0, parsed None |
| A | ian-011 | parse_changed_constraints | arrival_soc_pct_min: gold 20.0, parsed None |
| A | ian-013 | parse_changed_constraints | min_power_kw: gold None, parsed 50.0; connectors_override: gold ['CCS1'], parsed None |
| A | ian-014 | parse_changed_constraints | arrival_soc_pct_min: gold 10.0, parsed None |
| A | ian-016 | parse_changed_constraints | arrival_soc_pct_min: gold 20.0, parsed None |
| A | ian-024 | parse_changed_constraints | max_detour_min: gold 0.0, parsed None |
| A | ian-025 | parse_changed_constraints | min_power_kw: gold 150.0, parsed None |
| A | manas-003 | parse_changed_constraints | min_power_kw: gold 50.0, parsed None |
| A | manas-005 | parse_changed_constraints | arrival_soc_pct_min: gold 20.0, parsed None |
| A | manas-006 | parse_changed_constraints | arrival_soc_pct_min: gold 30.0, parsed None |
| A | manas-012 | parse_changed_constraints | min_power_kw: gold 350.0, parsed None |
| A | manas-013 | parse_changed_constraints | arrival_soc_pct_min: gold 25.0, parsed None |
| A | manas-014 | parse_changed_constraints | arrival_soc_pct_min: gold 20.0, parsed None |
| A | manas-016 | parse_changed_constraints | arrival_soc_pct_min: gold 15.0, parsed None |
| A | manas-024 | parse_changed_constraints | max_detour_min: gold 0.0, parsed None |
| A | manas-025 | parse_changed_constraints | max_detour_min: gold 3.0, parsed None |
| A | manas-028 | parse_changed_constraints | arrival_soc_pct_min: gold 25.0, parsed None |
| B | aarya-023 | oracle_search_incomplete | max_labels (40000) hit; 4 plan(s) found; excluded from front-based metrics |
| B | hriday-015 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| B | hriday-016 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| B | hriday-017 | oracle_search_incomplete | max_labels (40000) hit; 4 plan(s) found; excluded from front-based metrics |
| B | hriday-018 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| B | hriday-029 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| B | hriday-030 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| B | ian-018 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| B | manas-016 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| B | manas-017 | oracle_search_incomplete | max_labels (40000) hit; 3 plan(s) found; excluded from front-based metrics |
| B | manas-018 | oracle_search_incomplete | max_labels (40000) hit; 3 plan(s) found; excluded from front-based metrics |
| B | manas-028 | oracle_search_incomplete | max_labels (40000) hit; 1 plan(s) found; excluded from front-based metrics |
| B | hriday-002 | oracle_feasible_planner_rejected | oracle witness ['afdc:63047'] |
| C | aarya-023 | oracle_search_incomplete | max_labels (40000) hit; 4 plan(s) found; excluded from front-based metrics |
| C | hriday-015 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| C | hriday-016 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| C | hriday-017 | oracle_search_incomplete | max_labels (40000) hit; 4 plan(s) found; excluded from front-based metrics |
| C | hriday-018 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| C | hriday-029 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| C | hriday-030 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| C | ian-018 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| C | manas-016 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| C | manas-017 | oracle_search_incomplete | max_labels (40000) hit; 3 plan(s) found; excluded from front-based metrics |
| C | manas-018 | oracle_search_incomplete | max_labels (40000) hit; 3 plan(s) found; excluded from front-based metrics |
| C | manas-028 | oracle_search_incomplete | max_labels (40000) hit; 1 plan(s) found; excluded from front-based metrics |
| C | hriday-002 | oracle_feasible_planner_rejected | oracle witness ['afdc:63047'] |
| C-noKG | aarya-023 | oracle_search_incomplete | max_labels (40000) hit; 4 plan(s) found; excluded from front-based metrics |
| C-noKG | hriday-015 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| C-noKG | hriday-016 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| C-noKG | hriday-017 | oracle_search_incomplete | max_labels (40000) hit; 4 plan(s) found; excluded from front-based metrics |
| C-noKG | hriday-018 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| C-noKG | hriday-029 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| C-noKG | hriday-030 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| C-noKG | ian-018 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| C-noKG | manas-016 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| C-noKG | manas-017 | oracle_search_incomplete | max_labels (40000) hit; 3 plan(s) found; excluded from front-based metrics |
| C-noKG | manas-018 | oracle_search_incomplete | max_labels (40000) hit; 3 plan(s) found; excluded from front-based metrics |
| C-noKG | manas-028 | oracle_search_incomplete | max_labels (40000) hit; 1 plan(s) found; excluded from front-based metrics |
| C-noKG | hriday-002 | oracle_feasible_planner_rejected | oracle witness ['afdc:63047'] |

## LLM GPU residency

Share of the LLM held in GPU memory (size_vram / size from Ollama /api/ps), read before each item after the harness renewed keep_alive. Configs without an LLM record none.

| Config | Items recorded | Min | Median | Max | Keep-alive | Warm-up (s) |
|---|---|---|---|---|---|---|
| A | 0 | n/a | n/a | n/a | n/a | n/a |
| B | 100 | 0.703 | 0.703 | 0.703 | 2h | 0.400 |
| C | 100 | 0.703 | 0.703 | 0.703 | 2h | 7.180 |
| C-noKG | 100 | 0.703 | 0.703 | 0.703 | 2h | 7.260 |

## Run metadata

| Config | Git commit | Model | Digest | Ollama | Prompt | Ranker | Snapshot |
|---|---|---|---|---|---|---|---|
| A | e810f934b0fe853c4a44813d3f03fdaecb7c8643 | n/a | n/a | n/a | n/a | weighted | snapshot:96dfbabb4837 (frozen, created 2026-11-26T00:55:26.616109+00:00) |
| B | e810f934b0fe853c4a44813d3f03fdaecb7c8643 | qwen3:8b | 500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41 | 0.40.18 | extract_v2 | weighted | snapshot:96dfbabb4837 (frozen, created 2026-11-26T00:55:26.616109+00:00) |
| C | e810f934b0fe853c4a44813d3f03fdaecb7c8643 | qwen3:8b | 500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41 | 0.40.18 | extract_v2 | weighted+evidence | snapshot:96dfbabb4837 (frozen, created 2026-11-26T00:55:26.616109+00:00) |
| C-noKG | e810f934b0fe853c4a44813d3f03fdaecb7c8643 | qwen3:8b | 500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41 | 0.40.18 | extract_v2 | weighted+evidence | snapshot:96dfbabb4837 (frozen, created 2026-11-26T00:55:26.616109+00:00) |
