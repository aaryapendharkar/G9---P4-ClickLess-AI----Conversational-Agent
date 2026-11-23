# Evaluation report: shakedown_deepti_20261121T063824Z

Split: n/a
Configs: A, B, C. Bootstrap: 10000 resamples, seed 573, percentile 95 % CI.

## Headline

Value [95 % CI]. n/a = metric block missing or not implemented; [CI n/a] = the metric block reports no CI.

| Config | n | Field F1 (micro) | Exact match | Clarification F1 | Silent-assumption rate | Error rate | Plan feasibility | Hard-violation rate | Grounding (claim support) | Freshness mislabel rate |
|---|---|---|---|---|---|---|---|---|---|---|
| A | 30 | 0.579 [0.529, 0.629] | 0.000 [0.000, 0.000] | 0.421 [0.118, 0.667] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.480 [CI n/a] | 0.000 [CI n/a] | n/a | n/a |
| B | 30 | 0.608 [0.559, 0.658] | 0.033 [0.000, 0.100] | 0.615 [0.200, 0.889] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.680 [CI n/a] | 0.000 [CI n/a] | n/a | n/a |
| C | 30 | 0.608 [0.559, 0.658] | 0.033 [0.000, 0.100] | 0.615 [0.200, 0.889] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.680 [CI n/a] | 0.000 [CI n/a] | 1.000 [CI n/a] | 0.000 [CI n/a] |

## LLM parser fallback

Items an LLM parser handled, and how many it handed to the rules fallback (timeout, unreachable endpoint, or two invalid responses). Reasons are the parser's logged warnings.

| Config | Parser | LLM-parsed | Fallbacks | Fallback rate | Reasons |
|---|---|---|---|---|---|
| A | rules | 0 | 0 | n/a | no LLM parser |
| B | llm | 30 | 0 | 0.000 | - |
| C | llm | 30 | 0 | 0.000 | - |

## Paired comparisons

McNemar on per-item successes (b = first config only, c = second only); Wilcoxon signed-rank on per-item scores. Items both configs ran.

| Pair | Outcome | Test | n | First | Second | b | c | Statistic | p |
|---|---|---|---|---|---|---|---|---|---|
| A vs B | exact_match | McNemar | 30 | 0.000 | 0.033 | 0 | 1 | 0.000 | 1.0000 |
| A vs B | clarification_correct | McNemar | 30 | 0.633 | 0.833 | 0 | 6 | 0.000 | 0.0312 |
| A vs B | no_error | McNemar | 30 | 1.000 | 1.000 | 0 | 0 | 0.000 | 1.0000 |
| A vs B | field_f1 | Wilcoxon | 30 | 0.579 | 0.607 | n/a | n/a | 56.000 | 0.1981 |
| A vs B | latency_ms | Wilcoxon | 30 | 344.920 | 14972.655 | n/a | n/a | 0.000 | 0.0000 |
| B vs C | exact_match | McNemar | 30 | 0.033 | 0.033 | 0 | 0 | 0.000 | 1.0000 |
| B vs C | clarification_correct | McNemar | 30 | 0.833 | 0.833 | 0 | 0 | 0.000 | 1.0000 |
| B vs C | no_error | McNemar | 30 | 1.000 | 1.000 | 0 | 0 | 0.000 | 1.0000 |
| B vs C | field_f1 | Wilcoxon | 30 | 0.607 | 0.607 | n/a | n/a | n/a | n/a |
| B vs C | latency_ms | Wilcoxon | 30 | 14972.655 | 19769.618 | n/a | n/a | 30.000 | 0.0000 |

## Breakdowns

### By author

| Config | author | n | Error rate | Field F1 | Exact match | Clarification F1 | Silent-assumption |
|---|---|---|---|---|---|---|---|
| A | deepti | 30 | 0.000 | 0.579 | 0.000 | 0.421 | 0.000 |
| B | deepti | 30 | 0.000 | 0.608 | 0.033 | 0.615 | 0.000 |
| C | deepti | 30 | 0.000 | 0.608 | 0.033 | 0.615 | 0.000 |

### By category

| Config | category | n | Error rate | Field F1 | Exact match | Clarification F1 | Silent-assumption |
|---|---|---|---|---|---|---|---|
| A | accessibility | 3 | 0.000 | 0.571 | 0.000 | 0.000 | n/a |
| A | conflicting_prefs | 3 | 0.000 | 0.556 | 0.000 | n/a | n/a |
| A | incomplete | 4 | 0.000 | 0.552 | 0.000 | 1.000 | 0.000 |
| A | multi_stop | 4 | 0.000 | 0.600 | 0.000 | 0.000 | n/a |
| A | priority_tradeoff | 2 | 0.000 | 0.636 | 0.000 | n/a | n/a |
| A | rural | 5 | 0.000 | 0.571 | 0.000 | 0.000 | n/a |
| A | sparse_charging | 4 | 0.000 | 0.581 | 0.000 | 0.000 | n/a |
| A | urban | 5 | 0.000 | 0.578 | 0.000 | 0.000 | n/a |
| B | accessibility | 3 | 0.000 | 0.500 | 0.000 | n/a | n/a |
| B | conflicting_prefs | 3 | 0.000 | 0.564 | 0.000 | n/a | n/a |
| B | incomplete | 4 | 0.000 | 0.571 | 0.000 | 1.000 | 0.000 |
| B | multi_stop | 4 | 0.000 | 0.652 | 0.000 | 0.000 | n/a |
| B | priority_tradeoff | 2 | 0.000 | 0.636 | 0.000 | n/a | n/a |
| B | rural | 5 | 0.000 | 0.615 | 0.000 | 0.000 | n/a |
| B | sparse_charging | 4 | 0.000 | 0.750 | 0.250 | 0.000 | n/a |
| B | urban | 5 | 0.000 | 0.560 | 0.000 | 0.000 | n/a |
| C | accessibility | 3 | 0.000 | 0.500 | 0.000 | n/a | n/a |
| C | conflicting_prefs | 3 | 0.000 | 0.564 | 0.000 | n/a | n/a |
| C | incomplete | 4 | 0.000 | 0.571 | 0.000 | 1.000 | 0.000 |
| C | multi_stop | 4 | 0.000 | 0.652 | 0.000 | 0.000 | n/a |
| C | priority_tradeoff | 2 | 0.000 | 0.636 | 0.000 | n/a | n/a |
| C | rural | 5 | 0.000 | 0.615 | 0.000 | 0.000 | n/a |
| C | sparse_charging | 4 | 0.000 | 0.750 | 0.250 | 0.000 | n/a |
| C | urban | 5 | 0.000 | 0.560 | 0.000 | 0.000 | n/a |

### By route_type

| Config | route_type | n | Error rate | Field F1 | Exact match | Clarification F1 | Silent-assumption |
|---|---|---|---|---|---|---|---|
| A | mixed | 12 | 0.000 | 0.584 | 0.000 | 0.727 | 0.000 |
| A | rural | 12 | 0.000 | 0.589 | 0.000 | 0.000 | n/a |
| A | urban | 6 | 0.000 | 0.545 | 0.000 | 0.000 | n/a |
| B | mixed | 12 | 0.000 | 0.602 | 0.000 | 0.889 | 0.000 |
| B | rural | 12 | 0.000 | 0.651 | 0.083 | 0.000 | n/a |
| B | urban | 6 | 0.000 | 0.533 | 0.000 | 0.000 | n/a |
| C | mixed | 12 | 0.000 | 0.602 | 0.000 | 0.889 | 0.000 |
| C | rural | 12 | 0.000 | 0.651 | 0.083 | 0.000 | n/a |
| C | urban | 6 | 0.000 | 0.533 | 0.000 | 0.000 | n/a |

## Oracle search

Items whose oracle Pareto search stopped early (time budget or max_labels). They get an `oracle_search_incomplete` finding and are left out of the front-based metrics.

| Config | Items searched | Incomplete | Reasons | Budget (s) | Items |
|---|---|---|---|---|---|
| A | 12 | 2 | max_labels: 2 | 600 | deepti-010 deepti-024 |
| B | 17 | 2 | max_labels: 2 | 600 | deepti-010 deepti-024 |
| C | 17 | 2 | max_labels: 2 | 600 | deepti-010 deepti-024 |

## Findings (planner/oracle disagreements)

| Config | Item | Kind | Detail |
|---|---|---|---|
| A | deepti-010 | oracle_search_incomplete | max_labels (40000) hit; 2 plan(s) found; excluded from front-based metrics |
| A | deepti-024 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| A | deepti-004 | parse_changed_constraints | connectors_override: gold ['CCS1'], parsed None |
| A | deepti-007 | parse_changed_constraints | arrival_soc_pct_min: gold 25.0, parsed None |
| A | deepti-012 | parse_changed_constraints | min_power_kw: gold 100.0, parsed None |
| A | deepti-013 | parse_changed_constraints | arrival_soc_pct_min: gold 15.0, parsed None |
| A | deepti-014 | oracle_feasible_planner_rejected | oracle witness ['afdc:112782', 'afdc:112783', 'afdc:467015', 'afdc:329804', 'afdc:237636', 'afdc:318178', 'afdc:316906', 'afdc:331346', 'afdc:186197', 'afdc:434821', 'afdc:312598', 'afdc:472219', 'afdc:472057', 'afdc:391380', 'afdc:122346', 'afdc:472215'] |
| A | deepti-014 | parse_changed_constraints | arrival_soc_pct_min: gold 20.0, parsed None |
| A | deepti-015 | oracle_feasible_planner_rejected | oracle witness ['afdc:255437', 'afdc:329804', 'afdc:391249', 'afdc:101988', 'afdc:279334', 'afdc:470602', 'afdc:461144', 'afdc:186206', 'afdc:457673', 'afdc:354303', 'afdc:397446', 'afdc:117722', 'afdc:238674', 'afdc:391210', 'afdc:101984', 'afdc:102305'] |
| A | deepti-015 | parse_changed_constraints | arrival_soc_pct_min: gold 20.0, parsed None |
| A | deepti-018 | parse_changed_constraints | min_power_kw: gold 150.0, parsed None |
| A | deepti-026 | parse_changed_constraints | connectors_override: gold ['CHADEMO'], parsed None |
| B | deepti-010 | oracle_search_incomplete | max_labels (40000) hit; 2 plan(s) found; excluded from front-based metrics |
| B | deepti-024 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| B | deepti-014 | oracle_feasible_planner_rejected | oracle witness ['afdc:112782', 'afdc:112783', 'afdc:467015', 'afdc:329804', 'afdc:237636', 'afdc:318178', 'afdc:316906', 'afdc:331346', 'afdc:186197', 'afdc:434821', 'afdc:312598', 'afdc:472219', 'afdc:472057', 'afdc:391380', 'afdc:122346', 'afdc:472215'] |
| B | deepti-015 | oracle_feasible_planner_rejected | oracle witness ['afdc:255437', 'afdc:329804', 'afdc:391249', 'afdc:101988', 'afdc:279334', 'afdc:470602', 'afdc:461144', 'afdc:186206', 'afdc:457673', 'afdc:354303', 'afdc:397446', 'afdc:117722', 'afdc:238674', 'afdc:391210', 'afdc:101984', 'afdc:102305'] |
| B | deepti-016 | oracle_feasible_planner_rejected | oracle witness ['afdc:102157', 'afdc:466730', 'afdc:451616', 'afdc:149766', 'afdc:122296', 'afdc:463298', 'afdc:448406', 'afdc:446875', 'afdc:421384', 'afdc:190810', 'afdc:191563', 'afdc:429744', 'afdc:448461', 'afdc:448405', 'afdc:373052', 'afdc:102167', 'afdc:449940', 'afdc:253948', 'afdc:448703', 'afdc:466203', 'afdc:116801', 'afdc:190382', 'afdc:372600', 'afdc:446867', 'afdc:371475', 'afdc:228791', 'afdc:316920', 'afdc:261447', 'afdc:102166', 'afdc:170362', 'afdc:452304', 'afdc:459542', 'afdc:231690', 'afdc:316919', 'afdc:371966', 'afdc:46819', 'afdc:470435', 'afdc:466208', 'afdc:448708', 'afdc:461025', 'afdc:429934', 'afdc:466201', 'afdc:448706', 'afdc:377472', 'afdc:429933', 'afdc:351857', 'afdc:102155', 'afdc:121725', 'afdc:446877', 'afdc:203256', 'afdc:416761', 'afdc:451466', 'afdc:420202', 'afdc:350761', 'afdc:350760', 'afdc:350759', 'afdc:350758', 'afdc:367748', 'afdc:434784', 'afdc:462692', 'afdc:469501', 'afdc:456731', 'afdc:439496', 'afdc:472389', 'afdc:454253', 'afdc:372953', 'afdc:122884', 'afdc:102496', 'afdc:241259', 'afdc:102497', 'afdc:456753', 'afdc:358366', 'afdc:421114', 'afdc:421115', 'afdc:446595', 'afdc:221103', 'afdc:449287', 'afdc:459932', 'afdc:371481'] |
| B | deepti-024 | parse_changed_constraints | connectors_override: gold None, parsed ['CCS1'] |
| C | deepti-010 | oracle_search_incomplete | max_labels (40000) hit; 2 plan(s) found; excluded from front-based metrics |
| C | deepti-024 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| C | deepti-014 | oracle_feasible_planner_rejected | oracle witness ['afdc:112782', 'afdc:112783', 'afdc:467015', 'afdc:329804', 'afdc:237636', 'afdc:318178', 'afdc:316906', 'afdc:331346', 'afdc:186197', 'afdc:434821', 'afdc:312598', 'afdc:472219', 'afdc:472057', 'afdc:391380', 'afdc:122346', 'afdc:472215'] |
| C | deepti-015 | oracle_feasible_planner_rejected | oracle witness ['afdc:255437', 'afdc:329804', 'afdc:391249', 'afdc:101988', 'afdc:279334', 'afdc:470602', 'afdc:461144', 'afdc:186206', 'afdc:457673', 'afdc:354303', 'afdc:397446', 'afdc:117722', 'afdc:238674', 'afdc:391210', 'afdc:101984', 'afdc:102305'] |
| C | deepti-016 | oracle_feasible_planner_rejected | oracle witness ['afdc:102157', 'afdc:466730', 'afdc:451616', 'afdc:149766', 'afdc:122296', 'afdc:463298', 'afdc:448406', 'afdc:446875', 'afdc:421384', 'afdc:190810', 'afdc:191563', 'afdc:429744', 'afdc:448461', 'afdc:448405', 'afdc:373052', 'afdc:102167', 'afdc:449940', 'afdc:253948', 'afdc:448703', 'afdc:466203', 'afdc:116801', 'afdc:190382', 'afdc:372600', 'afdc:446867', 'afdc:371475', 'afdc:228791', 'afdc:316920', 'afdc:261447', 'afdc:102166', 'afdc:170362', 'afdc:452304', 'afdc:459542', 'afdc:231690', 'afdc:316919', 'afdc:371966', 'afdc:46819', 'afdc:470435', 'afdc:466208', 'afdc:448708', 'afdc:461025', 'afdc:429934', 'afdc:466201', 'afdc:448706', 'afdc:377472', 'afdc:429933', 'afdc:351857', 'afdc:102155', 'afdc:121725', 'afdc:446877', 'afdc:203256', 'afdc:416761', 'afdc:451466', 'afdc:420202', 'afdc:350761', 'afdc:350760', 'afdc:350759', 'afdc:350758', 'afdc:367748', 'afdc:434784', 'afdc:462692', 'afdc:469501', 'afdc:456731', 'afdc:439496', 'afdc:472389', 'afdc:454253', 'afdc:372953', 'afdc:122884', 'afdc:102496', 'afdc:241259', 'afdc:102497', 'afdc:456753', 'afdc:358366', 'afdc:421114', 'afdc:421115', 'afdc:446595', 'afdc:221103', 'afdc:449287', 'afdc:459932', 'afdc:371481'] |
| C | deepti-024 | parse_changed_constraints | connectors_override: gold None, parsed ['CCS1'] |

## Run metadata

| Config | Git commit | Model | Digest | Ollama | Prompt | Snapshot |
|---|---|---|---|---|---|---|
| A | f019c87554d688bc122f45a32a3dc651fb9d56b8 (dirty) | n/a | n/a | n/a | n/a | n/a |
| B | f019c87554d688bc122f45a32a3dc651fb9d56b8 (dirty) | qwen3:8b | 500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41 | 0.40.17 | extract_v2 | n/a |
| C | ab93502ad83bbef169e21b0140f81f4eb14fa05f | qwen3:8b | 500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41 | 0.40.17 | extract_v2 | n/a |
