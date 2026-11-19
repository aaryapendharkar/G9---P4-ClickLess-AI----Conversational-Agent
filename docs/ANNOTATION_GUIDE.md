# Annotation guide (draft v0.1 — owner: Ian; must match `src/evaluation/schema.py`)

## One item = one line of JSON
```json
{"id": "aarya-007", "author": "aarya", "category": "accessibility", "route_type": "rural",
 "text": "Driving my 2024 <curated model> from Tucson to Page tomorrow, 70% now, need a wheelchair accessible restroom and lunch, keep detours under 10 min",
 "gold_request": {"origin": "Tucson, AZ", "destination": "Page, AZ", "vehicle_id": "<id from vehicles.csv>",
                  "start_soc_pct": 70, "max_detour_min": 10,
                  "amenities": [{"category": "restroom", "wheelchair_required": true}, {"category": "food"}]},
 "clarification_required": false, "missing_safety_fields": [],
 "hard_constraints": {"max_detour_min": 10}, "soft_constraints": {"amenities": ["restroom_wheelchair", "food"]},
 "cross_checked_by": "manas"}
```

## Rules
1. **Annotate only what the text states.** No defaults in `gold_request` (e.g. no arrival SoC
   unless stated). The system's defaults are not the annotator's business. The only exception
   is the state in a place name, under the conditions in rule 4.
2. **Safety-critical fields**: origin, destination, vehicle, starting battery. If any is
   missing or ambiguous → `clarification_required: true` and list it in `missing_safety_fields`.
   Ambiguous examples: "my Tesla" (which model/year?), "half a tank", "Springfield".
3. `vehicle_id` must exist in `data/curated/vehicles.csv`. Unsupported vehicle → category
   `infeasible` with a note.
4. **Places** (`origin`, `destination`): write gold places in geocodable form, `"City, ST"`
   ("Flagstaff, AZ"), or `"Landmark, City, ST"` for a named place ("Denver International
   Airport, Denver, CO"). This is the one exception to rule 1, and it is narrow:
   - **Add the state** when the text doesn't name it but only one reading is plausible: the city
     is unambiguous ("Tucson" → "Tucson, AZ"), or the route clearly fixes it ("Bishop, CA to
     Mammoth Lakes" → "Mammoth Lakes, CA").
   - **Don't add a state** when the city is genuinely ambiguous (Portland, Jackson, Rochester,
     Springfield, Columbus) and nothing in the text settles it. Write the place as stated
     ("Rochester"), set `clarification_required: true` and list the field (`origin` or
     `destination`) in `missing_safety_fields` (rule 2). This holds whether the ambiguity was
     designed into the request or is accidental: an ambiguous place is never complete.
   - **Name the place, not a part of it**: no terminals, gates, floors or entrances. The
     geocoder must find the landmark itself ("Phoenix Sky Harbor International Airport, Phoenix,
     AZ", not "... Terminal 4, Phoenix, AZ"). Keep any finer detail in `notes`.
   - Never resolve an ambiguous city from your own knowledge of the trip ("Rochester to Buffalo"
     does not make Rochester NY unless the text says so). If you are unsure whether a city is
     ambiguous, treat it as ambiguous and say why in `notes`.
5. Amenities: one entry per requested category; `wheelchair_required` only when stated;
   `priority: "important"` only for explicit needs ("must", "need").
   "Accessible restroom" and "wheelchair-accessible restroom" are treated the same: set
   `wheelchair_required: true`. Only set it when the driver mentions accessibility.
6. Trade-off phrasing (any category, wherever the driver states it): add `"tradeoff_profile"`
   to `gold_request` — `fastest` ("in a hurry"), `comfort` ("no rush if there's good food"),
   otherwise omit. `priority_tradeoff` items must have it; items in other categories (e.g.
   `conflicting_prefs`) get it too when the text states a profile.
7. Percentages as numbers (`65`, not `"65%"`); minutes for detours.

## Categories
`urban`, `rural`, `sparse_charging` (long gaps between compatible stations), `multi_stop`
(needs ≥ 2 charges), `incomplete`, `conflicting_prefs` (e.g. "fastest possible but a sit-down
dinner"), `accessibility`, `priority_tradeoff`, `infeasible`.

## Cross-check and double annotation
- Every item is cross-checked by your learning buddy (ring order in `TEAM_PLAN.md`): fix typos,
  flag disagreements in `notes`, never silently change someone's gold.
- 40 items are independently re-annotated (blind) for Cohen's κ / field agreement
  (`evaluation/agreement.py`). Ian assigns them.
