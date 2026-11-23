# Evaluation report: shakedown_deepti_nokg_20261121T110125Z

Split: n/a
Configs: C. Bootstrap: 10000 resamples, seed 573, percentile 95 % CI.

## Headline

Value [95 % CI]. n/a = metric block missing or not implemented; [CI n/a] = the metric block reports no CI.

| Config | n | Field F1 (micro) | Exact match | Clarification F1 | Silent-assumption rate | Error rate | Plan feasibility | Hard-violation rate | Grounding (claim support) | Freshness mislabel rate |
|---|---|---|---|---|---|---|---|---|---|---|
| C | 30 | 0.608 [0.559, 0.658] | 0.033 [0.000, 0.100] | 0.615 [0.200, 0.889] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.680 [CI n/a] | 0.000 [CI n/a] | 1.000 [CI n/a] | 0.000 [CI n/a] |

## LLM parser fallback

Items an LLM parser handled, and how many it handed to the rules fallback (timeout, unreachable endpoint, or two invalid responses). Reasons are the parser's logged warnings.

| Config | Parser | LLM-parsed | Fallbacks | Fallback rate | Reasons |
|---|---|---|---|---|---|
| C | llm | 30 | 0 | 0.000 | - |

## Paired comparisons

McNemar on per-item successes (b = first config only, c = second only); Wilcoxon signed-rank on per-item scores. Items both configs ran.

- A vs B: n/a (A, B missing)
- B vs C: n/a (B missing)

## Breakdowns

### By author

| Config | author | n | Error rate | Field F1 | Exact match | Clarification F1 | Silent-assumption |
|---|---|---|---|---|---|---|---|
| C | deepti | 30 | 0.000 | 0.608 | 0.033 | 0.615 | 0.000 |

### By category

| Config | category | n | Error rate | Field F1 | Exact match | Clarification F1 | Silent-assumption |
|---|---|---|---|---|---|---|---|
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
| C | mixed | 12 | 0.000 | 0.602 | 0.000 | 0.889 | 0.000 |
| C | rural | 12 | 0.000 | 0.651 | 0.083 | 0.000 | n/a |
| C | urban | 6 | 0.000 | 0.533 | 0.000 | 0.000 | n/a |

## Oracle search

Items whose oracle Pareto search stopped early (time budget or max_labels). They get an `oracle_search_incomplete` finding and are left out of the front-based metrics.

| Config | Items searched | Incomplete | Reasons | Budget (s) | Items |
|---|---|---|---|---|---|
| C | 17 | 2 | max_labels: 2 | 600 | deepti-010 deepti-024 |

## Findings (planner/oracle disagreements)

| Config | Item | Kind | Detail |
|---|---|---|---|
| C | deepti-010 | oracle_search_incomplete | max_labels (40000) hit; 2 plan(s) found; excluded from front-based metrics |
| C | deepti-024 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| C | deepti-014 | oracle_feasible_planner_rejected | oracle witness ['afdc:112782', 'afdc:112783', 'afdc:467015', 'afdc:329804', 'afdc:237636', 'afdc:318178', 'afdc:316906', 'afdc:331346', 'afdc:186197', 'afdc:434821', 'afdc:312598', 'afdc:472219', 'afdc:472057', 'afdc:391380', 'afdc:122346', 'afdc:472215'] |
| C | deepti-015 | oracle_feasible_planner_rejected | oracle witness ['afdc:255437', 'afdc:329804', 'afdc:391249', 'afdc:101988', 'afdc:279334', 'afdc:470602', 'afdc:461144', 'afdc:186206', 'afdc:457673', 'afdc:354303', 'afdc:397446', 'afdc:117722', 'afdc:238674', 'afdc:391210', 'afdc:101984', 'afdc:102305'] |
| C | deepti-016 | oracle_feasible_planner_rejected | oracle witness ['afdc:102157', 'afdc:466730', 'afdc:451616', 'afdc:149766', 'afdc:122296', 'afdc:463298', 'afdc:448406', 'afdc:446875', 'afdc:421384', 'afdc:190810', 'afdc:191563', 'afdc:429744', 'afdc:448461', 'afdc:448405', 'afdc:373052', 'afdc:102167', 'afdc:449940', 'afdc:253948', 'afdc:448703', 'afdc:466203', 'afdc:116801', 'afdc:190382', 'afdc:372600', 'afdc:446867', 'afdc:371475', 'afdc:228791', 'afdc:316920', 'afdc:261447', 'afdc:102166', 'afdc:170362', 'afdc:452304', 'afdc:459542', 'afdc:231690', 'afdc:316919', 'afdc:371966', 'afdc:46819', 'afdc:470435', 'afdc:466208', 'afdc:448708', 'afdc:461025', 'afdc:429934', 'afdc:466201', 'afdc:448706', 'afdc:377472', 'afdc:429933', 'afdc:351857', 'afdc:102155', 'afdc:121725', 'afdc:446877', 'afdc:203256', 'afdc:416761', 'afdc:451466', 'afdc:420202', 'afdc:350761', 'afdc:350760', 'afdc:350759', 'afdc:350758', 'afdc:367748', 'afdc:434784', 'afdc:462692', 'afdc:469501', 'afdc:456731', 'afdc:439496', 'afdc:472389', 'afdc:454253', 'afdc:372953', 'afdc:122884', 'afdc:102496', 'afdc:241259', 'afdc:102497', 'afdc:456753', 'afdc:358366', 'afdc:421114', 'afdc:421115', 'afdc:446595', 'afdc:221103', 'afdc:449287', 'afdc:459932', 'afdc:371481'] |
| C | deepti-024 | parse_changed_constraints | connectors_override: gold None, parsed ['CCS1'] |

## Run metadata

| Config | Git commit | Model | Digest | Ollama | Prompt | Snapshot |
|---|---|---|---|---|---|---|
| C | 31d252e44225fcc3ad0468a05e6fbbc61035185d | qwen3:8b | 500a1f067a9f782620b40bee6f7b0c89e17ae61f686b92c24933e4ca4b2b8b41 | 0.40.17 | extract_v2 | n/a |
