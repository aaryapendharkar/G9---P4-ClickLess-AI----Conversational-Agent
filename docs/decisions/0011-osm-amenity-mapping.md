# ADR 0011: OpenStreetMap amenity mapping

- Status: accepted (2 approvals: Deepti 2026-11-09, Manas 2026-11-14)
- Date: 2026-11-03 (accepted 2026-11-14, issue #44)
- Owner: Aarya
- Approvers: Deepti, Manas

## Approvals
| Approver | Scope | Date | Record |
|---|---|---|---|
| Deepti | Parser categories and wheelchair/restroom rules | 2026-11-09 | [issue #44 comment](https://github.com/aaryapendharkar/G9---P4-ClickLess-AI----Conversational-Agent/issues/44#issuecomment-6742677715) |
| Manas | Retrieval/matching: categories, 400 m radius, accessibility tags | 2026-11-14 | [issue #44 comment](https://github.com/aaryapendharkar/G9---P4-ClickLess-AI----Conversational-Agent/issues/44#issuecomment-6882515614) |

Pre-freeze follow-up from Manas's approval: skip OSM elements (and their `#toilets` restroom) whose `access` is `private` or `no`.
## Context
Proposal §II: OSM supplies food, restrooms, wheelchair tags and opening hours, linked to
stations within 400 m; a missing accessibility tag means unknown, never accessible. The parser
already uses the fixed `AmenityCategory` enum (food, coffee, restroom, grocery, lodging, other).

## Decision
**Query.** One Overpass query per batch of 100 stations: for each station, `nwr(around:400,lat,lon)`
with a key/value regex over the mapped tags below, plus `nwr(around:...)["toilets"="yes"]`.
Points are never combined in one `around` (several points there form a polyline, not a set of
circles). Output `out meta center;` (ways/relations get a center point). Editor names, user ids
and changesets are stripped before raw batches are saved. Batches are cached on disk (reruns
resume), paced 2 s apart, retried with backoff on 429/504, and a batch whose response carries an
Overpass `remark` (timeout, incomplete data) aborts the build instead of being used.

**Categories** (first match wins; everything else → OTHER and is not stored):

| Category | OSM tags |
|---|---|
| food | `amenity=restaurant`, `amenity=fast_food`, `amenity=food_court` |
| coffee | `amenity=cafe`, `shop=coffee` |
| restroom | `amenity=toilets` |
| grocery | `shop=supermarket`, `shop=convenience`, `shop=greengrocer` |
| lodging | `tourism=hotel`, `tourism=motel` |

**Restrooms inside venues.** An element tagged `toilets=yes` (gas station, restaurant, shop)
also yields a separate RESTROOM amenity `osm:<type>/<id>#toilets`. Its accessibility comes from
`toilets:wheelchair`, not the venue's `wheelchair` tag (that describes the entrance). It is
skipped when `toilets:access` is `private`, `no` or `customers_only_staff`.

**Wheelchair.** `yes`/`designated` → yes, `limited` → limited, `no` → no, anything else or
missing → unknown. Only `yes` counts toward `restroom_wheelchair` (TRADEOFFS.md).

**Provenance.** `source=OSM`, `source_record_id=<type>/<id>`, OSM URL, `retrieved_at` = batch
time, `source_updated_at` = element timestamp, `status=cached`. Kept tags: amenity, shop,
tourism, cuisine, brand, name, opening_hours, wheelchair, toilets, toilets:wheelchair,
toilets:access, access.

## Consequences
- Matching (Manas) sees cuisine/brand text for TF-IDF/SBERT; categories stay those of the enum.
- Convenience stores count as grocery: a deliberate, generous reading of "grocery"; revisit if
  the relevance labels disagree.
- Coverage varies by region (proposal §II limitation); the manifest reports amenity counts.
