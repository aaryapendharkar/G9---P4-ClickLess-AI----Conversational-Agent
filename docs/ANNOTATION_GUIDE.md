# Annotation guide (draft v0.1 — owner: Ian; must match `src/evaluation/schema.py`)

## One item = one line of JSON
```json
{"id": "aarya-007", "author": "aarya", "category": "accessibility", "route_type": "rural",
 "text": "Driving my 2024 <curated model> from Tucson to Page tomorrow, 70% now, need a wheelchair accessible restroom and lunch, keep detours under 10 min",
 "gold_request": {"origin": "Tucson", "destination": "Page", "vehicle_id": "<id from vehicles.csv>",
                  "start_soc_pct": 70, "max_detour_min": 10,
                  "amenities": [{"category": "restroom", "wheelchair_required": true}, {"category": "food"}]},
 "clarification_required": false, "missing_safety_fields": [],
 "hard_constraints": {"max_detour_min": 10}, "soft_constraints": {"amenities": ["restroom_wheelchair", "food"]},
 "cross_checked_by": "manas"}
```

## Rules
1. **Annotate only what the text states.** No defaults in `gold_request` (e.g. no arrival SoC
   unless stated). The system's defaults are not the annotator's business.
2. **Safety-critical fields**: origin, destination, vehicle, starting battery. If any is
   missing or ambiguous → `clarification_required: true` and list it in `missing_safety_fields`.
   Ambiguous examples: "my Tesla" (which model/year?), "half a tank", "Springfield".
3. `vehicle_id` must exist in `data/curated/vehicles.csv`. Unsupported vehicle → category
   `infeasible` with a note.
4. Places: write the place text as you'd geocode it ("Flagstaff, AZ").
5. Amenities: one entry per requested category; `wheelchair_required` only when stated;
   `priority: "important"` only for explicit needs ("must", "need").
6. Trade-off phrasing (category `priority_tradeoff`): add `"tradeoff_profile"` to
   `gold_request` — `fastest` ("in a hurry"), `comfort` ("no rush if there's good food"),
   otherwise omit.
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
