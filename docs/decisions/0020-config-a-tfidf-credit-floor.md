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

Issue #166 compares credit policies on the frozen DEV split only. Earlier analysis established
the reporting machinery and showed that changing the credit interpretation can change some
recommendations. The chosen `0.45` value passed unit, integration, snapshot smoke and clean
full-DEV runs. The held-out TEST split was not used to select or tune the policy.

## Decision

Config A transforms each requested preference's best TF-IDF score before converting it into
minutes-equivalent amenity credit:

```text
credit_score(s) = 0                    when s = 0
credit_score(s) = max(s, 0.45)         when s > 0
amenity_credit = amenity_value_min[p] * credit_score(s)
```

The zero case is essential: an absent or category-rejected amenity earns no credit. The policy is
configured under `ranking.tfidf_credit` in `configs/config_a.yaml`. Configs B and C keep their raw
SBERT scores and therefore do not inherit a TF-IDF-specific calibration rule.

Both the logistic-regression ranker and its weighted fallback use the same production helper.
The independent evaluator reproduces the documented rule without importing ranking code, so it
can still detect implementation drift while avoiding false formula/ranker mismatches.

## DEV evidence

The final comparison ran from clean commit `06d82c9` against frozen snapshot
`snapshot:96dfbabb4837`. It loaded all 50 DEV items: 40 produced verified plans, 10 were skipped
or unplanned, and none raised an analysis error.

| policy | recommendations changed from raw | mean recommended amenity credit (min) | mean recommended amenity satisfaction |
|---|---:|---:|---:|
| raw | 0 | 3.189 | 0.598 |
| floor 0.45 | 0 | 3.880 | 0.642 |
| full credit for any positive score | 2 | 7.775 | 0.875 |
| normalize within the request | 4 | 6.550 | 0.826 |

The floor modestly changes the interpretation of positive TF-IDF matches without changing which
itinerary is recommended on DEV. Full credit changes two recommendations. Normalization changes
four, including two items whose preference phrase came from the category fallback rather than a
parsed phrase. This makes the floor the least disruptive tested calibration, but the run does not
show that its recommendations are objectively better: the policy-selection evidence is stability
and bounded credit, not recommendation-accuracy labels.

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
- Acceptance now requires review by Manas and Ian and their recorded approvals. Any later change
  to `0.45` requires new DEV evidence and an ADR update.
