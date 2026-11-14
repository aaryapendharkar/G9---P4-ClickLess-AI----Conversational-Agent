# Trade-offs: how they are weighted and shown to the driver

> TA feedback: *"Consider adding more detail on how trade-offs between detour distance, charging
> duration and preferred amenities are weighted and presented to the driver."*

Owners: Hriday (model + selection), Deepti (language in/out), Manas (match scores), Aarya (power
and capacity data), Ian (evaluation). Code: `ranking/cost.py`, `ranking/tradeoffs.py`,
`planning/charging_time.py`, `explain/tradeoff_presenter.py`, `nlu/priority_phrases.py`,
`configs/tradeoffs.yaml`.

## 1. Ground rule: trade-offs only between *feasible* plans

Connector compatibility, physical reachability, arrival battery, reserve and the detour limit are
hard constraints. They are never traded against comfort. Trade-offs only rank plans that the
verifier already accepted. (If nothing is feasible, the separate, frozen relaxation order applies.)

## 2. One unit: minutes-equivalent

Every term is expressed in minutes so that the driver can understand it and we can explain it:

```
cost = w_drive · drive_min                (includes detour driving)
     + w_charge · charge_min              (estimated, §3)
     + detour_extra_weight · detour_min   (extra dislike of leaving the highway)
     + stop_overhead_min · number_of_stops
     − Σ over requested preferences p:  amenity_value_min[p] · best_match_score(p)
     − evidence_credit_min · evidence_coverage          (Config C only)
```

* A preference counts only if the user **asked for it**, once per trip, at its best stop, scaled
  by the matcher's score in [0, 1] (TF-IDF in A, SBERT in B/C).
* Accessibility: only amenities tagged `wheelchair=yes` count toward `restroom_wheelchair`;
  `unknown` never counts (proposal §II).
* One-sentence explanation for the report: *"In the balanced profile we treat a lunch stop the
  driver asked for as worth up to 15 extra minutes, and an accessible restroom as worth 20."*

### Profiles (configs/tradeoffs.yaml)

| term | balanced | fastest | comfort |
|---|---|---|---|
| charge-time weight | 1.0 | 1.0 | 0.6 (waiting while eating hurts less) |
| detour extra weight | 0.5 | 1.0 | 0.3 |
| overhead per stop (min) | 5 | 8 | 3 |
| food / coffee / restroom (min) | 15 / 8 / 10 | 3 / 2 / 3 | 30 / 15 / 15 |
| accessible restroom (min) | 20 | 15 | 30 |

The driver picks a profile in words ("I'm in a hurry", "no rush if there's good food") or with
explicit overrides ("food matters more than time" → `amenity_value_min.food` up). Values are
starting points: **tune on DEV only, then freeze before the test run** (record in an ADR).

## 3. Charging duration (estimate)

The proposal says we do not model vehicle-specific charging curves. We keep that honesty but
still need a duration to trade against amenities and detours:

* peak power = min(port power, vehicle max DC power); AC (L2/L1) ports × 0.9 AC efficiency;
* constant power up to 80 % SoC, then 40 % of peak above it (DC); + 3 min session overhead;
* unknown station power (the source did not report it, ADR 0010): the estimate uses a
  configurable fallback per charger level, `charging_model.unknown_power_kw` in
  `configs/energy.yaml` (DC fast 50 kW, L2 6.6 kW, L1 1.4 kW). It is **not guaranteed
  conservative**: an unknown station may be slower, so its duration can be underestimated. The
  station's power stays unknown in the data and is shown as "unknown power", and it never
  satisfies an explicit minimum-power request;
* always shown as "about N min (estimate)";
* robustness: Ian re-ranks with charge time × 0.75 and × 1.25 and reports how often the
  recommendation changes. That scaling does not cover a wrong fallback (a 50 kW guess at a
  25 kW station doubles the time), so stops with unknown power get a separate unknown-power
  sensitivity check;
* the Nissan LEAF is excluded from charge-time accuracy and sensitivity results (ADR 0009); its
  feasibility results stay in scope.

## 4. What the driver sees

1. **Recommended** = lowest cost under the active profile, with the cost breakdown.
2. Up to **two alternatives**, each the best plan on one axis (Fastest, Best amenities, Least
   detour), only if it is Pareto-optimal on (total time, detour, charge time, amenity
   satisfaction) and differs meaningfully (≥ 5 min or ≥ 0.25 satisfaction).
3. Each alternative is described by its **deltas** against Recommended and the preferences it
   **gains/loses**. Every number comes from `Alternative.deltas` / `PlanCost` — never recomputed in
   text — so the grounding metric can check it.
4. The driver can pick an option or say "prioritize X"; that changes the profile/weights and
   **re-ranks the already-verified plans** (no re-planning needed, so the answer is instant).

Current walking-skeleton output on the synthetic fixtures:

```
Stop at Fixture Camp Verde Hub (CCS1, 50 kW): charge 26% -> 43% in about 18 min (estimate).
  Nearby (requested): Verde Fixture Kitchen, Verde Fixture Restroom
Arrive in Flagstaff with about 22% after ~161 min total (3 min of detour).
Why this ranks first (balanced priorities, in minutes-equivalent): driving 143 + charging 18
  + detour penalty 2 + stop overhead 5 - requested amenities 35 = 133.
Other options:
  Fastest (Fixture Plaza Anthem): -10 min total; +0 min detour; -10 min charging; loses food, restroom_wheelchair.
```

Hriday's learning-swap presenter now translates signed deltas into natural
directions such as *"about 10 min quicker"*, suppresses differences below one
minute, shows at most two numerical differences, labels charging time as an
estimate, and translates internal preference keys such as `restroom_wheelchair`
before showing them to the driver. Every displayed number still comes directly
from `Alternative.deltas`.

## 5. Evaluation additions (Ian)

| metric | what it checks |
|---|---|
| Pareto validity | presented alternatives are non-dominated among the oracle's feasible plans |
| delta faithfulness | numbers in trade-off sentences equal the computed deltas (C's LLM included) |
| weight sensitivity | Kendall τ of rankings across profiles and ±20 % weights; recommendation flip rate |
| charge-time sensitivity | same with charge time × 0.75 / × 1.25 |
| unknown-power sensitivity | same with `unknown_power_kw` varied, for plans with unknown-power stops (ADR 0010) |
| priority-phrase accuracy | predicted profile vs gold on the `priority_tradeoff` benchmark items |

Each author adds 2 `priority_tradeoff` items (10 total). Optional, if time allows: 3–5 classmates
choose between Recommended/alternative on 5 scripted trips; report agreement with our ranking.

## 6. Text to add to the final report (draft)

> Feasible itineraries are ranked by a minutes-equivalent cost that combines driving, estimated
> charging and detour time with stop overhead, minus credits for satisfied amenity preferences
> the driver requested. Weights come from three named profiles selectable in conversation and
> were fixed on development data. The driver sees the recommended plan with its cost breakdown
> and up to two Pareto-optimal alternatives described by their time differences and the
> preferences they gain or lose; all stated differences are taken from verified plan fields.

## 7. Open decisions (decide by Oct 25, record as ADRs)

- Is "wheelchair-accessible restroom" a soft preference (current) or a hard constraint when the
  user says "need"? Proposal relaxes amenities first — be explicit about accessibility.
- Default arrival SoC when unstated (currently 20 %) and the reserve (10 %).

Resolved charging policy: balanced and fastest charge just enough for the next
leg. Comfort may top up while eating only when the stop satisfies food requested
by the driver. The target is the charging model's pre-taper threshold, capped by
the maximum departure SoC, so the policy does not intentionally add slower
tapered charging.
