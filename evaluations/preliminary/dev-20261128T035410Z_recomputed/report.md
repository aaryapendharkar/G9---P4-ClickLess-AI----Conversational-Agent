# Evaluation report: 20261128T035410Z_recomputed

Split: dev
Configs: A, B, C, C-noKG. Bootstrap: 10000 resamples, seed 573, percentile 95 % CI.

## Headline

Value [95 % CI]. n/a = metric block missing or not implemented; [CI n/a] = the metric block reports no CI.
Field F1 and exact match compare places at city level; (strict) also requires the gold's state, which annotators add for geocoding. Parse / planning error rates are over all items.
Hard-violation rate (vs gold): measured against the gold request (includes parser errors); verifier disagreements: A 0, B 0, C 0, C-noKG 0 (findings where the planner's verifier and the oracle, on the request the verifier saw, disagree).
Plan feasibility and hard-violation CIs resample items: A from result JSON; B from result JSON; C from result JSON; C-noKG from result JSON.
KG evidence coverage: share of the recommended plan's displayed stop facts (station name, connector, power, amenities) supported by the result's own KG evidence, mean over items with a charging stop. KG-cited claim share: claims citing at least one KG fact, over all claims.

| Config | n | Field F1 (micro) | Field F1 (strict) | Exact match | Exact match (strict) | Clarification F1 | Silent-assumption rate | Invented-constraint rate | Parse error rate | Planning error rate | Plan feasibility | Hard-violation rate (vs gold) | Grounding (claim support) | KG evidence coverage | KG-cited claim share | Freshness mislabel rate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | 50 | 0.874 [0.835, 0.911] | 0.631 [0.603, 0.658] | 0.440 [0.300, 0.580] | 0.000 [0.000, 0.000] | 0.432 [0.211, 0.615] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.500 [0.350, 0.650] | 0.038 [0.000, 0.125] | n/a | 0.000 [0.000, 0.000] | n/a | n/a |
| B | 50 | 0.967 [0.942, 0.988] | 0.652 [0.623, 0.684] | 0.840 [0.720, 0.940] | 0.040 [0.000, 0.100] | 0.700 [0.400, 0.900] | 0.125 [0.000, 0.417] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.900 [0.800, 0.975] | 0.000 [0.000, 0.000] | n/a | 0.000 [0.000, 0.000] | n/a | n/a |
| C | 50 | 0.967 [0.942, 0.988] | 0.652 [0.623, 0.684] | 0.840 [0.720, 0.940] | 0.040 [0.000, 0.100] | 0.700 [0.400, 0.900] | 0.125 [0.000, 0.417] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.900 [0.800, 0.975] | 0.000 [0.000, 0.000] | 1.000 [CI n/a] | 1.000 [1.000, 1.000] | 0.404 [0.346, 0.455] | 0.000 [CI n/a] |
| C-noKG | 50 | 0.967 [0.942, 0.988] | 0.652 [0.623, 0.684] | 0.840 [0.720, 0.940] | 0.040 [0.000, 0.100] | 0.700 [0.400, 0.900] | 0.125 [0.000, 0.417] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.900 [0.800, 0.975] | 0.000 [0.000, 0.000] | 1.000 [CI n/a] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [CI n/a] |

## LLM parser fallback

Items an LLM parser handled, and how many it handed to the rules fallback (timeout, unreachable endpoint, or two invalid responses). Reasons are the parser's logged warnings.

| Config | Parser | LLM-parsed | Fallbacks | Fallback rate | Reasons |
|---|---|---|---|---|---|
| A | rules | 0 | 0 | n/a | no LLM parser |
| B | llm | 50 | 0 | 0.000 | - |
| C | llm | 50 | 0 | 0.000 | - |
| C-noKG | llm | 50 | 0 | 0.000 | - |

## Paired comparisons

McNemar on per-item successes (b = first config only, c = second only); Wilcoxon signed-rank on per-item scores. Items both configs ran.

| Pair | Outcome | Test | n | First | Second | b | c | Statistic | p |
|---|---|---|---|---|---|---|---|---|---|
| A vs B | exact_match | McNemar | 50 | 0.440 | 0.840 | 1 | 21 | 1.000 | 0.0000 |
| A vs B | clarification_correct | McNemar | 50 | 0.580 | 0.880 | 1 | 16 | 1.000 | 0.0003 |
| A vs B | no_error | McNemar | 50 | 1.000 | 1.000 | 0 | 0 | 0.000 | 1.0000 |
| A vs B | no_invented_constraint | McNemar | 50 | 1.000 | 1.000 | 0 | 0 | 0.000 | 1.0000 |
| A vs B | got_valid_plan | McNemar | 40 | 0.500 | 0.900 | 0 | 16 | 0.000 | 0.0000 |
| A vs B | field_f1 | Wilcoxon | 50 | 0.860 | 0.968 | n/a | n/a | 18.000 | 0.0000 |
| A vs B | latency_ms | Wilcoxon | 50 | 623.382 | 10389.624 | n/a | n/a | 0.000 | 0.0000 |
| A vs C | exact_match | McNemar | 50 | 0.440 | 0.840 | 1 | 21 | 1.000 | 0.0000 |
| A vs C | clarification_correct | McNemar | 50 | 0.580 | 0.880 | 1 | 16 | 1.000 | 0.0003 |
| A vs C | no_error | McNemar | 50 | 1.000 | 1.000 | 0 | 0 | 0.000 | 1.0000 |
| A vs C | no_invented_constraint | McNemar | 50 | 1.000 | 1.000 | 0 | 0 | 0.000 | 1.0000 |
| A vs C | got_valid_plan | McNemar | 40 | 0.500 | 0.900 | 0 | 16 | 0.000 | 0.0000 |
| A vs C | field_f1 | Wilcoxon | 50 | 0.860 | 0.968 | n/a | n/a | 18.000 | 0.0000 |
| A vs C | latency_ms | Wilcoxon | 50 | 623.382 | 10756.901 | n/a | n/a | 0.000 | 0.0000 |
| B vs C | exact_match | McNemar | 50 | 0.840 | 0.840 | 0 | 0 | 0.000 | 1.0000 |
| B vs C | clarification_correct | McNemar | 50 | 0.880 | 0.880 | 0 | 0 | 0.000 | 1.0000 |
| B vs C | no_error | McNemar | 50 | 1.000 | 1.000 | 0 | 0 | 0.000 | 1.0000 |
| B vs C | no_invented_constraint | McNemar | 50 | 1.000 | 1.000 | 0 | 0 | 0.000 | 1.0000 |
| B vs C | got_valid_plan | McNemar | 40 | 0.900 | 0.900 | 0 | 0 | 0.000 | 1.0000 |
| B vs C | field_f1 | Wilcoxon | 50 | 0.968 | 0.968 | n/a | n/a | n/a | n/a |
| B vs C | latency_ms | Wilcoxon | 50 | 10389.624 | 10756.901 | n/a | n/a | 188.000 | 0.0000 |
| C vs C-noKG | exact_match | McNemar | 50 | 0.840 | 0.840 | 0 | 0 | 0.000 | 1.0000 |
| C vs C-noKG | clarification_correct | McNemar | 50 | 0.880 | 0.880 | 0 | 0 | 0.000 | 1.0000 |
| C vs C-noKG | no_error | McNemar | 50 | 1.000 | 1.000 | 0 | 0 | 0.000 | 1.0000 |
| C vs C-noKG | no_invented_constraint | McNemar | 50 | 1.000 | 1.000 | 0 | 0 | 0.000 | 1.0000 |
| C vs C-noKG | got_valid_plan | McNemar | 40 | 0.900 | 0.900 | 0 | 0 | 0.000 | 1.0000 |
| C vs C-noKG | field_f1 | Wilcoxon | 50 | 0.968 | 0.968 | n/a | n/a | n/a | n/a |
| C vs C-noKG | latency_ms | Wilcoxon | 50 | 10756.901 | 10673.715 | n/a | n/a | 616.000 | 0.8408 |
| C vs C-noKG | kg_evidence_coverage | Wilcoxon | 31 | 1.000 | 0.000 | n/a | n/a | 0.000 | 0.0000 |
| C vs C-noKG | kg_claim_share | Wilcoxon | 37 | 0.368 | 0.000 | n/a | n/a | 0.000 | 0.0000 |

## Handoff correctness (offline, #185)

For each ok item the harness approved a COPY of the result for the recommended option (no real approval, no navigation) and saved the Google Maps URLs and the expected stops. They are checked by evaluation.metrics.handoff, which parses the URLs and does not use the handoff builder. URL checks: parses, stops in order, each stop once, segment boundaries, no extra stops. Matches plan: the approved stops are the saved recommended itinerary's. Endpoints: the URLs start at the request's origin and end at its destination. Approved: exactly one approval entry in the run folder's audit log. Run-wide: no saved result is approved or carries URLs, and no item without status ok has a handoff.

| Config | ok items | Scored | URL checks | Matches plan | Endpoints | Approved (audit) | Fully correct | Saved results approved | Non-ok items with a handoff | Handoff errors |
|---|---|---|---|---|---|---|---|---|---|---|
| A | 21 | 21 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 (21/21) | 0/50 | 0/29 | 0 |
| B | 37 | 37 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 (37/37) | 0/50 | 0/13 | 0 |
| C | 37 | 37 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 (37/37) | 0/50 | 0/13 | 0 |
| C-noKG | 37 | 37 | 1.000 | 1.000 | 1.000 | 1.000 | 1.000 (37/37) | 0/50 | 0/13 | 0 |

## Latency (#185)

Measured on: one Windows laptop (RTX 2060 6 GB; qwen3:8b ~70% on GPU, rest on CPU). LLM GPU share (median): B 0.703, C 0.703, C-noKG 0.703. Latency depends on the machine; compare configs within this run only.

Request latency (`latency_ms`) runs from just before pipeline.run() until it returns or raises: parse, route, retrieve, plan+verify, rank, explain. It excludes the one-off LLM warm-up before the first item and the offline handoff check. All rows count, errors included (their count is shown); routed items are those that got past parsing to routing (rejected requests stop after parsing). Stage laps come from each result's timings_ms; an error row keeps the laps recorded before the exception. p50 / p95: numpy linear interpolation.

| Config | Scope | n | p50 (ms) | p95 (ms) | Mean (ms) | Max (ms) | Error rows |
|---|---|---|---|---|---|---|---|
| A | request (all rows) | 50 | 1 | 2,466 | 623 | 9,169 | 0 |
| A | request (non-error rows) | 50 | 1 | 2,466 | 623 | 9,169 | 0 |
| A | request (routed items) | 21 | 1,105 | 2,570 | 1,483 | 9,169 | 0 |
| A | stage: parse | 50 | 1 | 1 | 1 | 10 | 0 |
| A | stage: route | 21 | 4 | 19 | 39 | 713 | 0 |
| A | stage: retrieve | 21 | 488 | 2,013 | 623 | 2,264 | 0 |
| A | stage: plan+verify | 21 | 177 | 2,149 | 801 | 8,880 | 0 |
| A | stage: rank | 21 | 1 | 14 | 6 | 47 | 0 |
| A | stage: explain | 50 | 0 | 0 | 1 | 44 | 0 |
| B | request (all rows) | 50 | 9,849 | 19,159 | 10,390 | 23,821 | 0 |
| B | request (non-error rows) | 50 | 9,849 | 19,159 | 10,390 | 23,821 | 0 |
| B | request (routed items) | 38 | 10,457 | 20,083 | 11,369 | 23,821 | 0 |
| B | stage: parse | 50 | 9,398 | 12,442 | 8,879 | 13,671 | 0 |
| B | stage: route | 38 | 9 | 25 | 10 | 28 | 0 |
| B | stage: retrieve | 38 | 481 | 2,292 | 850 | 11,695 | 0 |
| B | stage: plan+verify | 38 | 119 | 4,908 | 917 | 12,139 | 0 |
| B | stage: rank | 37 | 1 | 684 | 197 | 3,868 | 0 |
| B | stage: explain | 50 | 0 | 0 | 1 | 32 | 0 |
| C | request (all rows) | 50 | 10,058 | 19,781 | 10,757 | 21,991 | 0 |
| C | request (non-error rows) | 50 | 10,058 | 19,781 | 10,757 | 21,991 | 0 |
| C | request (routed items) | 38 | 11,277 | 20,020 | 11,848 | 21,991 | 0 |
| C | stage: parse | 50 | 9,448 | 12,694 | 8,980 | 14,182 | 0 |
| C | stage: route | 38 | 10 | 29 | 12 | 33 | 0 |
| C | stage: retrieve | 38 | 784 | 2,871 | 1,185 | 9,652 | 0 |
| C | stage: plan+verify | 38 | 104 | 5,257 | 850 | 9,156 | 0 |
| C | stage: rank | 37 | 3 | 1,053 | 272 | 4,352 | 0 |
| C | stage: explain | 50 | 0 | 0 | 1 | 30 | 0 |
| C-noKG | request (all rows) | 50 | 10,153 | 19,176 | 10,674 | 22,356 | 0 |
| C-noKG | request (non-error rows) | 50 | 10,153 | 19,176 | 10,674 | 22,356 | 0 |
| C-noKG | request (routed items) | 38 | 10,697 | 19,410 | 11,661 | 22,356 | 0 |
| C-noKG | stage: parse | 50 | 9,670 | 13,007 | 9,245 | 14,374 | 0 |
| C-noKG | stage: route | 38 | 9 | 29 | 12 | 30 | 0 |
| C-noKG | stage: retrieve | 38 | 485 | 2,294 | 793 | 8,680 | 0 |
| C-noKG | stage: plan+verify | 38 | 116 | 5,025 | 876 | 10,236 | 0 |
| C-noKG | stage: rank | 37 | 1 | 685 | 185 | 3,449 | 0 |
| C-noKG | stage: explain | 50 | 0 | 0 | 1 | 40 | 0 |

## Provenance completeness (#186)

Over the displayed charging-stop facts of each recommended plan (station name, connector, power, amenities: the facts KG evidence coverage counts), pooled over items. A fact is complete when a fact in the result's own KG evidence supports it and that fact's provenance has source, source_record_id and retrieved_at; a displayed fact without supporting evidence is never complete (missing: evidence). Validate-required counts only source and retrieved_at, the fields clickless.data.validate requires of every record. source_updated_at is optional there (OSM does not publish it) and is reported as a share. CI: bootstrap over items.

| Config | Items with stops | Displayed facts | With evidence | Complete | Validate-required | source_updated_at | Missing: evidence / source / record id / retrieved_at / updated_at | Note |
|---|---|---|---|---|---|---|---|---|
| A | 18 | 146 | 0 | 0.000 [0.000, 0.000] | 0.000 | 0.000 | 146 / 0 / 0 / 0 / 0 | no evidence in any result (by design: no KG) |
| B | 31 | 263 | 0 | 0.000 [0.000, 0.000] | 0.000 | 0.000 | 263 / 0 / 0 / 0 / 0 | no evidence in any result (by design: no KG) |
| C | 31 | 263 | 263 | 1.000 [1.000, 1.000] | 1.000 | 0.433 | 0 / 0 / 0 / 0 / 149 | - |
| C-noKG | 31 | 263 | 0 | 0.000 [0.000, 0.000] | 0.000 | 0.000 | 263 / 0 / 0 / 0 / 0 | no evidence in any result (by design: no KG) |

## Errors by stage and type

No errors.

## Breakdowns

### By author

| Config | author | n | Parse err | Planning err | Field F1 | F1 (strict) | Exact match | EM (strict) | Clarification F1 | Silent-assumption | Invented-constraint |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A | aarya | 15 | 0.000 | 0.000 | 0.881 | 0.643 | 0.467 | 0.000 | 0.462 | 0.000 | 0.000 |
| A | deepti | 30 | 0.000 | 0.000 | 0.890 | 0.636 | 0.467 | 0.000 | 0.421 | 0.000 | 0.000 |
| A | hriday | 4 | 0.000 | 0.000 | 0.778 | 0.556 | 0.250 | 0.000 | 0.500 | 0.000 | 0.000 |
| A | manas | 1 | 0.000 | 0.000 | 0.571 | 0.571 | 0.000 | 0.000 | 0.000 | n/a | 0.000 |
| B | aarya | 15 | 0.000 | 0.000 | 0.947 | 0.632 | 0.800 | 0.000 | 0.667 | 0.000 | 0.000 |
| B | deepti | 30 | 0.000 | 0.000 | 0.987 | 0.649 | 0.933 | 0.033 | 0.889 | 0.000 | 0.000 |
| B | hriday | 4 | 0.000 | 0.000 | 0.933 | 0.756 | 0.500 | 0.250 | 0.000 | 1.000 | 0.000 |
| B | manas | 1 | 0.000 | 0.000 | 0.800 | 0.600 | 0.000 | 0.000 | 0.000 | n/a | 0.000 |
| C | aarya | 15 | 0.000 | 0.000 | 0.947 | 0.632 | 0.800 | 0.000 | 0.667 | 0.000 | 0.000 |
| C | deepti | 30 | 0.000 | 0.000 | 0.987 | 0.649 | 0.933 | 0.033 | 0.889 | 0.000 | 0.000 |
| C | hriday | 4 | 0.000 | 0.000 | 0.933 | 0.756 | 0.500 | 0.250 | 0.000 | 1.000 | 0.000 |
| C | manas | 1 | 0.000 | 0.000 | 0.800 | 0.600 | 0.000 | 0.000 | 0.000 | n/a | 0.000 |
| C-noKG | aarya | 15 | 0.000 | 0.000 | 0.947 | 0.632 | 0.800 | 0.000 | 0.667 | 0.000 | 0.000 |
| C-noKG | deepti | 30 | 0.000 | 0.000 | 0.987 | 0.649 | 0.933 | 0.033 | 0.889 | 0.000 | 0.000 |
| C-noKG | hriday | 4 | 0.000 | 0.000 | 0.933 | 0.756 | 0.500 | 0.250 | 0.000 | 1.000 | 0.000 |
| C-noKG | manas | 1 | 0.000 | 0.000 | 0.800 | 0.600 | 0.000 | 0.000 | 0.000 | n/a | 0.000 |

### By category

| Config | category | n | Parse err | Planning err | Field F1 | F1 (strict) | Exact match | EM (strict) | Clarification F1 | Silent-assumption | Invented-constraint |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A | accessibility | 5 | 0.000 | 0.000 | 0.923 | 0.692 | 0.600 | 0.000 | 0.000 | n/a | 0.000 |
| A | conflicting_prefs | 4 | 0.000 | 0.000 | 0.939 | 0.612 | 0.250 | 0.000 | n/a | n/a | 0.000 |
| A | incomplete | 8 | 0.000 | 0.000 | 0.909 | 0.545 | 0.750 | 0.000 | 1.000 | 0.000 | 0.000 |
| A | multi_stop | 7 | 0.000 | 0.000 | 0.917 | 0.667 | 0.571 | 0.000 | 0.000 | n/a | 0.000 |
| A | priority_tradeoff | 4 | 0.000 | 0.000 | 0.909 | 0.636 | 0.500 | 0.000 | 0.000 | n/a | 0.000 |
| A | rural | 7 | 0.000 | 0.000 | 0.944 | 0.639 | 0.714 | 0.000 | 0.000 | n/a | 0.000 |
| A | sparse_charging | 7 | 0.000 | 0.000 | 0.679 | 0.607 | 0.000 | 0.000 | 0.000 | n/a | 0.000 |
| A | urban | 8 | 0.000 | 0.000 | 0.783 | 0.638 | 0.125 | 0.000 | 0.000 | n/a | 0.000 |
| B | accessibility | 5 | 0.000 | 0.000 | 1.000 | 0.643 | 1.000 | 0.000 | n/a | n/a | 0.000 |
| B | conflicting_prefs | 4 | 0.000 | 0.000 | 0.923 | 0.615 | 0.500 | 0.000 | n/a | n/a | 0.000 |
| B | incomplete | 8 | 0.000 | 0.000 | 0.982 | 0.596 | 0.875 | 0.000 | 0.933 | 0.125 | 0.000 |
| B | multi_stop | 7 | 0.000 | 0.000 | 1.000 | 0.658 | 1.000 | 0.000 | n/a | n/a | 0.000 |
| B | priority_tradeoff | 4 | 0.000 | 0.000 | 1.000 | 0.636 | 1.000 | 0.000 | n/a | n/a | 0.000 |
| B | rural | 7 | 0.000 | 0.000 | 1.000 | 0.649 | 1.000 | 0.000 | n/a | n/a | 0.000 |
| B | sparse_charging | 7 | 0.000 | 0.000 | 0.973 | 0.784 | 0.857 | 0.286 | 0.000 | n/a | 0.000 |
| B | urban | 8 | 0.000 | 0.000 | 0.878 | 0.610 | 0.500 | 0.000 | 0.000 | n/a | 0.000 |
| C | accessibility | 5 | 0.000 | 0.000 | 1.000 | 0.643 | 1.000 | 0.000 | n/a | n/a | 0.000 |
| C | conflicting_prefs | 4 | 0.000 | 0.000 | 0.923 | 0.615 | 0.500 | 0.000 | n/a | n/a | 0.000 |
| C | incomplete | 8 | 0.000 | 0.000 | 0.982 | 0.596 | 0.875 | 0.000 | 0.933 | 0.125 | 0.000 |
| C | multi_stop | 7 | 0.000 | 0.000 | 1.000 | 0.658 | 1.000 | 0.000 | n/a | n/a | 0.000 |
| C | priority_tradeoff | 4 | 0.000 | 0.000 | 1.000 | 0.636 | 1.000 | 0.000 | n/a | n/a | 0.000 |
| C | rural | 7 | 0.000 | 0.000 | 1.000 | 0.649 | 1.000 | 0.000 | n/a | n/a | 0.000 |
| C | sparse_charging | 7 | 0.000 | 0.000 | 0.973 | 0.784 | 0.857 | 0.286 | 0.000 | n/a | 0.000 |
| C | urban | 8 | 0.000 | 0.000 | 0.878 | 0.610 | 0.500 | 0.000 | 0.000 | n/a | 0.000 |
| C-noKG | accessibility | 5 | 0.000 | 0.000 | 1.000 | 0.643 | 1.000 | 0.000 | n/a | n/a | 0.000 |
| C-noKG | conflicting_prefs | 4 | 0.000 | 0.000 | 0.923 | 0.615 | 0.500 | 0.000 | n/a | n/a | 0.000 |
| C-noKG | incomplete | 8 | 0.000 | 0.000 | 0.982 | 0.596 | 0.875 | 0.000 | 0.933 | 0.125 | 0.000 |
| C-noKG | multi_stop | 7 | 0.000 | 0.000 | 1.000 | 0.658 | 1.000 | 0.000 | n/a | n/a | 0.000 |
| C-noKG | priority_tradeoff | 4 | 0.000 | 0.000 | 1.000 | 0.636 | 1.000 | 0.000 | n/a | n/a | 0.000 |
| C-noKG | rural | 7 | 0.000 | 0.000 | 1.000 | 0.649 | 1.000 | 0.000 | n/a | n/a | 0.000 |
| C-noKG | sparse_charging | 7 | 0.000 | 0.000 | 0.973 | 0.784 | 0.857 | 0.286 | 0.000 | n/a | 0.000 |
| C-noKG | urban | 8 | 0.000 | 0.000 | 0.878 | 0.610 | 0.500 | 0.000 | 0.000 | n/a | 0.000 |

### By route_type

| Config | route_type | n | Parse err | Planning err | Field F1 | F1 (strict) | Exact match | EM (strict) | Clarification F1 | Silent-assumption | Invented-constraint |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A | mixed | 21 | 0.000 | 0.000 | 0.911 | 0.634 | 0.524 | 0.000 | 0.737 | 0.000 | 0.000 |
| A | rural | 19 | 0.000 | 0.000 | 0.857 | 0.637 | 0.421 | 0.000 | 0.000 | n/a | 0.000 |
| A | urban | 10 | 0.000 | 0.000 | 0.824 | 0.612 | 0.300 | 0.000 | 0.250 | 0.000 | 0.000 |
| B | mixed | 21 | 0.000 | 0.000 | 0.981 | 0.632 | 0.905 | 0.000 | 1.000 | 0.000 | 0.000 |
| B | rural | 19 | 0.000 | 0.000 | 0.990 | 0.696 | 0.947 | 0.105 | 0.000 | n/a | 0.000 |
| B | urban | 10 | 0.000 | 0.000 | 0.889 | 0.606 | 0.500 | 0.000 | 0.000 | 1.000 | 0.000 |
| C | mixed | 21 | 0.000 | 0.000 | 0.981 | 0.632 | 0.905 | 0.000 | 1.000 | 0.000 | 0.000 |
| C | rural | 19 | 0.000 | 0.000 | 0.990 | 0.696 | 0.947 | 0.105 | 0.000 | n/a | 0.000 |
| C | urban | 10 | 0.000 | 0.000 | 0.889 | 0.606 | 0.500 | 0.000 | 0.000 | 1.000 | 0.000 |
| C-noKG | mixed | 21 | 0.000 | 0.000 | 0.981 | 0.632 | 0.905 | 0.000 | 1.000 | 0.000 | 0.000 |
| C-noKG | rural | 19 | 0.000 | 0.000 | 0.990 | 0.696 | 0.947 | 0.105 | 0.000 | n/a | 0.000 |
| C-noKG | urban | 10 | 0.000 | 0.000 | 0.889 | 0.606 | 0.500 | 0.000 | 0.000 | 1.000 | 0.000 |

## Invented constraints

Items whose parsed request has a hard constraint (connector, minimum power, maximum detour, minimum arrival SoC) that the gold request does not have (#145). Separate from silent assumptions, which only cover incomplete requests.

None.

## Oracle search

Items whose oracle Pareto search stopped early (time budget or max_labels). They get an `oracle_search_incomplete` finding and are left out of the front-based metrics.

| Config | Items searched | Incomplete | Reasons | Budget (s) | Items |
|---|---|---|---|---|---|
| A | 20 | 5 | max_labels: 5 | 600 | aarya-016 aarya-028 deepti-010 deepti-024 hriday-023 |
| B | 36 | 8 | max_labels: 8 | 600 | aarya-016 aarya-017 aarya-028 deepti-010 deepti-016 deepti-017 deepti-024 hriday-023 |
| C | 36 | 8 | max_labels: 8 | 600 | aarya-016 aarya-017 aarya-028 deepti-010 deepti-016 deepti-017 deepti-024 hriday-023 |
| C-noKG | 36 | 8 | max_labels: 8 | 600 | aarya-016 aarya-017 aarya-028 deepti-010 deepti-016 deepti-017 deepti-024 hriday-023 |

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
| A | aarya-016 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| A | aarya-028 | oracle_search_incomplete | max_labels (40000) hit; 1 plan(s) found; excluded from front-based metrics |
| A | deepti-010 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| A | deepti-024 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| A | hriday-023 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| A | deepti-004 | parse_changed_constraints | connectors_override: gold ['CCS1'], parsed None |
| A | deepti-012 | parse_changed_constraints | min_power_kw: gold 100.0, parsed None |
| A | deepti-013 | parse_changed_constraints | arrival_soc_pct_min: gold 15.0, parsed None |
| A | deepti-018 | parse_changed_constraints | min_power_kw: gold 150.0, parsed None |
| A | deepti-026 | parse_changed_constraints | connectors_override: gold ['CHADEMO'], parsed None |
| A | hriday-011 | parse_changed_constraints | arrival_soc_pct_min: gold 15.0, parsed None |
| A | hriday-012 | parse_changed_constraints | arrival_soc_pct_min: gold 20.0, parsed None; min_power_kw: gold 100.0, parsed None |
| A | manas-001 | parse_changed_constraints | arrival_soc_pct_min: gold 25.0, parsed None |
| B | aarya-016 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| B | aarya-017 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| B | aarya-028 | oracle_search_incomplete | max_labels (40000) hit; 1 plan(s) found; excluded from front-based metrics |
| B | deepti-010 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| B | deepti-016 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| B | deepti-017 | oracle_search_incomplete | max_labels (40000) hit; 4 plan(s) found; excluded from front-based metrics |
| B | deepti-024 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| B | hriday-023 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| C | aarya-016 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| C | aarya-017 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| C | aarya-028 | oracle_search_incomplete | max_labels (40000) hit; 1 plan(s) found; excluded from front-based metrics |
| C | deepti-010 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| C | deepti-016 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| C | deepti-017 | oracle_search_incomplete | max_labels (40000) hit; 4 plan(s) found; excluded from front-based metrics |
| C | deepti-024 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| C | hriday-023 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| C-noKG | aarya-016 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| C-noKG | aarya-017 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| C-noKG | aarya-028 | oracle_search_incomplete | max_labels (40000) hit; 1 plan(s) found; excluded from front-based metrics |
| C-noKG | deepti-010 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| C-noKG | deepti-016 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| C-noKG | deepti-017 | oracle_search_incomplete | max_labels (40000) hit; 4 plan(s) found; excluded from front-based metrics |
| C-noKG | deepti-024 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| C-noKG | hriday-023 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |

## LLM GPU residency

Share of the LLM held in GPU memory (size_vram / size from Ollama /api/ps), read before each item after the harness renewed keep_alive. Configs without an LLM record none.

| Config | Items recorded | Min | Median | Max | Keep-alive | Warm-up (s) |
|---|---|---|---|---|---|---|
| A | 0 | n/a | n/a | n/a | n/a | n/a |
| B | 50 | 0.703 | 0.703 | 0.703 | 2h | 0.450 |
| C | 50 | 0.703 | 0.703 | 0.703 | 2h | 11.150 |
| C-noKG | 50 | 0.703 | 0.703 | 0.703 | 2h | 7.300 |

## Run metadata

| Config | Git commit | Model | Digest | Ollama | Prompt | Ranker | Snapshot |
|---|---|---|---|---|---|---|---|
| A | 697eb0892a36919e044d46f16e5951dfcea47731 | n/a | n/a | n/a | n/a | weighted | snapshot:96dfbabb4837 (frozen, created 2026-11-26T00:55:26.616109+00:00) |
| B | 697eb0892a36919e044d46f16e5951dfcea47731 | qwen3:8b | 500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41 | 0.40.18 | extract_v2 | weighted | snapshot:96dfbabb4837 (frozen, created 2026-11-26T00:55:26.616109+00:00) |
| C | 697eb0892a36919e044d46f16e5951dfcea47731 | qwen3:8b | 500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41 | 0.40.18 | extract_v2 | weighted+evidence | snapshot:96dfbabb4837 (frozen, created 2026-11-26T00:55:26.616109+00:00) |
| C-noKG | 697eb0892a36919e044d46f16e5951dfcea47731 | qwen3:8b | 500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41 | 0.40.18 | extract_v2 | weighted+evidence | snapshot:96dfbabb4837 (frozen, created 2026-11-26T00:55:26.616109+00:00) |
