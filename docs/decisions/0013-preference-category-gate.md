# ADR 0013: Category gate for preference matching

- Status: proposed — needs the approvals listed under Approvals before merge
- Date: 2026-11-08
- Owner: Manas (retrieval)
- Approvers: Hriday (planning and ranking read the scores), Deepti (the parser assigns the category the gate trusts)

## Context
A preference matcher returns `score(preference, amenity)` in [0, 1]. Three places read it:
- **Planning:** `satisfied_preferences` keeps the best score per preference. Every amenity with a
  score > 0 goes into `ChargingStop.amenity_ids`, and the explanation lists those amenities as
  "Nearby (requested)".
- **Ranking cost:** the best score per preference key earns the amenity credit.
- **Trade-off alternatives:** a preference counts as met at `presentation.satisfied_threshold: 0.5`
  in `configs/tradeoffs.yaml`.

Config A's TF-IDF placeholder scores 1.0 inside the requested category and 0.0 outside it. In
PR #39 as first opened, the Sentence-BERT matcher returned raw cosine, which gives other categories
real credit. Measured with the pinned MiniLM on the fixtures (10 preference phrases x 8 amenities):

| Pairs | Lowest score | Highest score |
|---|---|---|
| same category (24) | 0.28 | 0.77 |
| other category (56) | 0.13 | 0.45 |

25 of the 56 other-category pairs score at or above the lowest same-category score. Run on the
demo request, Config B recommended Anthem and listed Fixture Coffee Bar under "Nearby (requested)"
as the lunch spot and the wheelchair-accessible restroom (food 0.25, restroom_wheelchair 0.45). So
the explanation made a false claim. The TF-IDF TODO had left open whether category is a hard gate
or a feature.

## Decision
An amenity can satisfy a preference only if `amenity.category == preference.category`. Every
matcher returns 0.0 for any other amenity before it computes any similarity. Inside the category,
the matcher's text similarity ranks amenities: Sentence-BERT cosine, and TF-IDF cosine once it is
implemented (1.0 until then).

- **One implementation:** `retrieval/category_gate.py::passes_category_gate`. `TfidfMatcher.score`
  and `EmbeddingMatcher.score` both call it first. A future matcher must call it too.
  `tests/retrieval/test_category_gate.py` checks the matcher of every configuration (A, B, C).
- **Planning's rule stays:** "score > 0 means matched". With the gate, it means "an amenity of the
  requested category that the wheelchair rule allows".
- **The wheelchair rule stays in planning:** unknown never counts.

Alternatives considered:
- **A satisfaction threshold on raw scores.** Rejected: the ranges overlap. Fixture Coffee Bar
  scores 0.45 for "restroom", higher than the correct match, Verde Fixture Kitchen, scores for
  "lunch" (0.36). Any threshold that drops the first also drops the second. A threshold is also
  model-specific: it cannot be one rule for TF-IDF and Sentence-BERT, and tuning it needs DEV data.
- **Category as a ranking feature (a boost).** Rejected: other categories still score > 0, so
  planning would still list them as requested.
- **The gate inside planning (`satisfied_preferences`, next to the wheelchair rule).** This would
  also be one place. But `planning/` is Hriday's folder, and the raw other-category scores would
  stay in `NearbyAmenity.match_scores` for ranking and any later reader. Gating at the source gives
  every reader the same scores.

## Consequences
- **Config B on the demo request** now recommends the same stop as Config A (Camp Verde). It lists
  Verde Fixture Kitchen and Verde Fixture Restroom (food 0.36, restroom_wheelchair 0.74). The
  ungated matcher never reached `main`, so no reported result changes.
- **The parser's category becomes decisive.** If the parser labels "a bathroom" as food, no
  restroom can satisfy it. That was already true for Config A; parser category accuracy matters
  more now (Deepti).
- **In-venue restrooms still count:** ADR 0011 stores them as separate RESTROOM amenities
  (`osm:<type>/<id>#toilets`).
- **`other` preferences are never satisfied,** because ADR 0011 stores no OTHER amenities. This was
  already true for Config A.
- **Inside a category, any amenity with a score > 0 satisfies the preference.** For example, a
  burger place satisfies "mexican food" and would be listed as requested (0.41 vs. 0.59 for the
  Mexican kitchen, so ranking gives the kitchen more credit). Config A behaves the same today. A
  phrase-level rule
  (such as a matching cuisine) would need DEV data and a model-specific threshold, so it is out of
  scope.
- **Open for Hriday and Ian (`configs/tradeoffs.yaml`, not changed here):**
  - `presentation.satisfied_threshold: 0.5` decides "gains" and "losses" for trade-off
    alternatives. It was set when scores were 1.0 or 0.0.
  - With Sentence-BERT, in-category scores are 0.28-0.77. So a real match can be listed as
    requested but not mentioned as a loss. On the demo, Config B's Fastest alternative "loses an
    accessible restroom" but not "a lunch spot"; Config A says both. This is an omission, not a
    false claim.
  - Proposal: use the planning rule (> 0) there as well, now that the gate guarantees the
    category.
- **Two approvals.** Shared pipeline behavior changes (scores for every configuration), so this ADR
  requires two approvals.

## Approvals
Each approver records approval here (name, date, PR comment or sync) before the status changes to
accepted. Merge needs both.

| Approver | Decision they approve | Required | Status |
|---|---|---|---|
| Hriday (planning, ranking) | Category is a hard gate in every matcher; planning keeps "score > 0 means matched" | yes | pending |
| Deepti (parser) | The parser's `AmenityCategory` alone decides which amenities can satisfy a preference | yes | pending |
