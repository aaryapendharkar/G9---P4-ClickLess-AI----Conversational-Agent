# ADR 0020: Config A TF-IDF amenity-credit floor

- Status: proposed
- Date: 2026-11-26
- Owner: Hriday (ranking and trade-offs)
- Approvers: Manas (TF-IDF retrieval), Ian (independent evaluation); two approvals required
- Related: issue #166, ADR 0013, `configs/config_a.yaml`

## Context

Config A's category-gated TF-IDF matcher correctly returns zero for amenities outside the
requested category. Inside the requested category, generic but valid phrases such as "lunch"
can receive a small positive cosine score. The minutes-equivalent ranker previously multiplied
amenity value directly by that raw score, so a valid requested amenity could receive almost no
credit even though the category gate had already established relevance.

Issue #166 compares credit policies on the frozen DEV split only. The saved Config B/C DEV
artifact in `evaluations/preliminary/dev-20261126/recommended_amenity_scores.csv` contains the
matcher scores for requested amenities on each recommended plan. Configs B and C recommended the
same plans and have identical positive-score populations. Across the 19 positive observations,
the mean is `0.5266` and the median is `0.5158`. The policy uses that median rounded to two decimal
places (`0.52`); it was not selected by sweeping thresholds or optimizing Config A outcomes. The
held-out TEST split was not used to select or tune the policy.

## Decision

Config A transforms each requested preference's best TF-IDF score before converting it into
minutes-equivalent amenity credit:

```text
credit_score(s) = 0                    when s = 0
credit_score(s) = max(s, 0.52)         when s > 0
amenity_credit = amenity_value_min[p] * credit_score(s)
```

The zero case is essential: an absent or category-rejected amenity earns no credit. The policy is
configured under `ranking.tfidf_credit` in `configs/config_a.yaml`. Configs B and C keep their raw
SBERT scores and therefore do not inherit a TF-IDF-specific calibration rule.

The calibration reduces a score-scale confound in the A/B/C comparison: Config A should be judged
on which amenities TF-IDF matches, rather than being penalized merely because positive TF-IDF
cosines tend to be smaller than positive SBERT cosines. It does not make the two score
distributions identical and does not grant credit to a zero or category-rejected match.
Because the rule uses `max(s, 0.52)`, positive scores below `0.52` receive equal ranking credit;
their raw ordering is preserved for diagnostics but not for cost or driver-facing satisfaction.
Scores above the floor retain their original ordering.

Both the logistic-regression ranker and its weighted fallback use the same production helper.
The trade-off selector uses the same transformed scores for its amenity axis and for the
driver-facing gained/lost preference lists, while the stored matcher scores remain raw.
The independent evaluator reproduces the documented rule without importing ranking code, so it
can still detect implementation drift while avoiding false formula/ranker mismatches.

## DEV evidence

The final comparison ran from clean commit `cd06605` against frozen snapshot
`snapshot:96dfbabb4837`. It loaded all 50 DEV items: 40 produced verified plans, 10 were skipped
or unplanned, and none raised an analysis error.

| policy | recommendations changed from raw | mean recommended amenity credit (min) | mean recommended amenity satisfaction |
|---|---:|---:|---:|
| raw | 0 | 3.189 | 0.598 |
| floor 0.52 | 1 | 4.266 | 0.671 |
| full credit for any positive score | 2 | 7.775 | 0.875 |
| normalize within the request | 4 | 6.550 | 0.826 |

The floor changes one recommendation, `deepti-008`: it selects a plan satisfying the requested
grocery preference for about `2.80` additional minutes, without changing the number of charging
stops. Full credit changes two recommendations. Normalization changes four, including two items
whose preference phrase came from the category fallback rather than a parsed phrase. The derived
floor is less disruptive than either alternative, but the run does not show that its recommendation
is objectively better: the policy-selection evidence is a predeclared scale-calibration rule,
bounded credit and DEV stability, not recommendation-accuracy labels.

## Alternatives considered

- **Raw TF-IDF score.** Simple and continuous, but weak positive scores make valid generic requests
  nearly irrelevant to ranking.
- **Full credit for every positive score.** Easy to explain, but removes useful distinctions between
  stronger and weaker phrase matches.
- **Normalize scores across the candidates in each request.** Preserves relative differences, but
  makes the same phrase worth different amounts depending on which candidates happen to be present.
- **Change the matcher output itself.** Rejected because retrieval should preserve its measured
  similarity; calibration belongs at the point where similarity becomes ranking credit.

## Consequences

- Config A can give a meaningful but bounded credit to weak positive in-category matches.
- Raw TF-IDF scores remain stored on amenities for diagnostics; only their ranking interpretation
  changes.
- Config B/C behavior remains unchanged.
- Existing Config A ranking results from before this policy are not directly comparable.
- The policy and its `0.52` value must be frozen before the held-out TEST run and before issue #79
  trains the logistic-regression ranker, so training and evaluation use the same feature scale.
- Acceptance now requires review by Manas and Ian and their recorded approvals. Any later change
  to `0.52` requires new DEV evidence and an ADR update.
