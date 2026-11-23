# Evaluation report: 20261123T042350Z

Split: dev
Configs: A. Bootstrap: 10000 resamples, seed 573, percentile 95 % CI.

## Headline

Value [95 % CI]. n/a = metric block missing or not implemented; [CI n/a] = the metric block reports no CI.
Field F1 and exact match compare places at city level; (strict) also requires the gold's state, which annotators add for geocoding. Parse / planning error rates are over all items.

| Config | n | Field F1 (micro) | Field F1 (strict) | Exact match | Exact match (strict) | Clarification F1 | Silent-assumption rate | Invented-constraint rate | Parse error rate | Planning error rate | Plan feasibility | Hard-violation rate | Grounding (claim support) | Freshness mislabel rate |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A | 50 | 0.874 [0.835, 0.911] | 0.631 [0.603, 0.658] | 0.440 [0.300, 0.580] | 0.000 [0.000, 0.000] | 0.432 [0.211, 0.615] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.000 [0.000, 0.000] | 0.500 [CI n/a] | 0.036 [CI n/a] | n/a | n/a |

## LLM parser fallback

Items an LLM parser handled, and how many it handed to the rules fallback (timeout, unreachable endpoint, or two invalid responses). Reasons are the parser's logged warnings.

| Config | Parser | LLM-parsed | Fallbacks | Fallback rate | Reasons |
|---|---|---|---|---|---|
| A | rules | 0 | 0 | n/a | no LLM parser |

## Paired comparisons

McNemar on per-item successes (b = first config only, c = second only); Wilcoxon signed-rank on per-item scores. Items both configs ran.


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

### By route_type

| Config | route_type | n | Parse err | Planning err | Field F1 | F1 (strict) | Exact match | EM (strict) | Clarification F1 | Silent-assumption | Invented-constraint |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A | mixed | 21 | 0.000 | 0.000 | 0.911 | 0.634 | 0.524 | 0.000 | 0.737 | 0.000 | 0.000 |
| A | rural | 19 | 0.000 | 0.000 | 0.857 | 0.637 | 0.421 | 0.000 | 0.000 | n/a | 0.000 |
| A | urban | 10 | 0.000 | 0.000 | 0.824 | 0.612 | 0.300 | 0.000 | 0.250 | 0.000 | 0.000 |

## Invented constraints

Items whose parsed request has a hard constraint (connector, minimum power, maximum detour, minimum arrival SoC) that the gold request does not have (#145). Separate from silent assumptions, which only cover incomplete requests.

None.

## Oracle search

Items whose oracle Pareto search stopped early (time budget or max_labels). They get an `oracle_search_incomplete` finding and are left out of the front-based metrics.

| Config | Items searched | Incomplete | Reasons | Budget (s) | Items |
|---|---|---|---|---|---|
| A | 20 | 5 | max_labels: 5 | 600 | aarya-016 aarya-028 deepti-010 deepti-024 hriday-023 |

## Findings (planner/oracle disagreements)

| Config | Item | Kind | Detail |
|---|---|---|---|
| A | aarya-016 | oracle_search_incomplete | max_labels (40000) hit; 0 plan(s) found; excluded from front-based metrics |
| A | aarya-028 | oracle_search_incomplete | max_labels (40000) hit; 1 plan(s) found; excluded from front-based metrics |
| A | deepti-010 | oracle_search_incomplete | max_labels (40000) hit; 2 plan(s) found; excluded from front-based metrics |
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

## LLM GPU residency

Share of the LLM held in GPU memory (size_vram / size from Ollama /api/ps), read before each item after the harness renewed keep_alive. Configs without an LLM record none.

| Config | Items recorded | Min | Median | Max | Keep-alive | Warm-up (s) |
|---|---|---|---|---|---|---|
| A | 0 | n/a | n/a | n/a | n/a | n/a |

## Run metadata

| Config | Git commit | Model | Digest | Ollama | Prompt | Snapshot |
|---|---|---|---|---|---|---|
| A | 5bf404970376b0dbb35a8efdd59c75ba8694f387 | n/a | n/a | n/a | n/a | snapshot:a352ddb69ca7 (draft, created 2026-11-23T03:32:21.129615+00:00) |
