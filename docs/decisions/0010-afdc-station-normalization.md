# ADR 0010: AFDC station normalization, unknown power, places, snapshot build, Maps limit

- Status: accepted (2 approvals: Hriday 2026-11-05, Ian 2026-11-09)
- Date: 2026-11-03 (accepted 2026-11-13, issue #34)
- Owner: Aarya
- Approvers: Hriday (planner consumes ports/power), Ian (validator, oracle inputs)

## Approvals
| Approver | Scope | Date | Record |
|---|---|---|---|
| Hriday | Planner, charging-time and hard-constraint semantics | 2026-11-05 | [issue #34 comment](https://github.com/aaryapendharkar/G9---P4-ClickLess-AI----Conversational-Agent/issues/34#issuecomment-6651698710) |
| Ian | Validator and independent-oracle compatibility | 2026-11-09 | [issue #34 comment](https://github.com/aaryapendharkar/G9---P4-ClickLess-AI----Conversational-Agent/issues/34#issuecomment-6735179056), [PR #35 review](https://github.com/aaryapendharkar/G9---P4-ClickLess-AI----Conversational-Agent/pull/35#pullrequestreview-5823161466) |

Both approvals asked for the unknown-power fallback to move from code to configuration and to stop
being called conservative. PR #35 made that change; the text below reflects it.

## Context
`data/afdc.py`, `data/geocode.py` and the snapshot build were stubs, and PR #26 left two
questions open: (1) duplicate station/vehicle ids vanish when `DataStore` builds its dicts;
(2) a DC port without `power_kw` is always a validation problem, with no "power unknown" flag.
Source: NREL All Stations API docs (`/api/alt-fuel-stations/v1`, checked 2026-11-03).

## Decision
**Download.** One request: `fuel_type=ELEC`, `access=public`, `status=E,T`, `country=US`,
`limit=all` (documented). Planned stations (`P`) are not downloaded; temporarily unavailable
ones are kept so the filters can explain why they were rejected. The raw response is saved
under `data/raw/afdc/` with retrieval time and parameters; the API key is never written to
disk or to error messages.

**Connector codes** (`data/connectors.py`): every documented code is mapped. `J1772`→J1772,
`J1772COMBO`→CCS1, `CHADEMO`→CHADEMO, `TESLA`→NACS ("NACS (J3400 AC/DC)"). Dropped on purpose:
`NEMA1450`, `NEMA515`, `NEMA520` (household outlets) and `J3271` (MCS, heavy trucks; no curated
vehicle). An undocumented code, status code, access code or charging level raises
`AfdcSchemaError`, so mappings change only on purpose.

**Ports.**
1. Records with `ev_charging_units`: one `ChargingPort` per (connector, level, power), power
   from `connectors.<code>.power_kw`, counts summed (missing count → 1). CCS1 and CHAdeMO are
   always DC fast; J1772 is always AC (L2, or L1 in a level-1 unit); NACS takes the unit's level.
2. Older records without units: `ev_connector_types` with the station counts. CCS1/CHAdeMO are
   DC fast; NACS is DC fast if the station has DC ports and is on the `Tesla` network (or has
   no L2 ports), otherwise L2 (destination chargers). DC counts are split evenly over the DC
   connector types. **Power is unknown (`power_kw = None`).**
3. Records with no supported connector or without coordinates are skipped and counted in the
   manifest (`sources.afdc.conversion.skipped`), as are stations outside the validator's US
   boxes (e.g. Guam).

**Unknown power (PR #26 question 2).** No contract change: `ChargingPort.power_kw is None`
already means "not reported by the source", and it stays `None` everywhere (station data, the
snapshot, the chosen `ChargingStop.port_power_kw`). What each part does with it:

- **Hard constraints:** unknown power never satisfies an explicit `min_power_kw` request.
  Without such a request, the station stays a candidate.
- **Charging-duration estimate only:** the planner uses a configured fallback per charger level,
  `charging_model.unknown_power_kw` in `configs/energy.yaml` (DC fast 50 kW, L2 6.6 kW,
  L1 1.4 kW; PR #35). This is an estimate, **not a guaranteed-conservative value**: an unknown
  station may deliver less than 50 kW, so the estimated duration can be optimistic. The fallback
  is never written back as station data.
- **Explanations:** such a stop is shown as "unknown power", and its duration as "(estimate)".
- **Evaluation:** the independent oracle reads the same frozen `unknown_power_kw` key (it does
  not import planner code) and adds an unknown-power sensitivity check, because the
  ×0.75/×1.25 charge-time scaling does not cover a 50 kW guess at a slower station.
- **Validator:** such ports are reported under a **warning** check `dc_power_unknown`;
  `blocking_problems()` excludes warnings, so they do not stop a freeze. Non-positive power is
  still a blocking `dc_power` problem.

`tests/integration/test_unknown_power_boundary.py` locks these properties in.

**Duplicate ids (PR #26 question 1).** `DataStore.__init__` raises `ValueError` on duplicate
station or vehicle ids instead of silently keeping the last one. AFDC conversion also drops and
counts repeated ids before that point.

**Places.** `data/geocode.py` geocodes the benchmark's origin/destination strings with OSM
Nominatim (1 request/s, cached in `data/cache/geocode/`). Only strings ending in a US state code
are looked up; bare ambiguous names ("Springfield") and fields marked missing stay unresolved so
the system asks. Keys: full lowercase string, plus the bare city when only one geocoded place has
that city name.

**Snapshot build.** `python -m clickless.data.build_snapshot` (or `make snapshot-build`) writes
`stations.json`, `amenities.json`, `vehicles.json`, `places.json` and `routes/` in the fixture
layout, loads them back through `DataStore`, validates, and writes `MANIFEST.json` only when
nothing blocking remains. The manifest records hashes, record counts, query parameters,
retrieval windows, conversion reports, warnings and provenance completeness.

**Selecting, checking and freezing a snapshot (issue #32).** Configs keep `data.source:
fixtures` by default; `CLICKLESS_DATA_SOURCE=snapshot` switches any config for one run.
`DataStore.from_config` refuses a snapshot whose `MANIFEST.json` is missing or whose files are
missing or changed. Builds are drafts (`"frozen": false`) unless run with `--freeze`, which is
refused until ADR 0009, 0010 and 0011 are all accepted.

**Data-status labels.** `status_label` appends "; may be out of date" when a cached record's
source update date is more than 365 days before its retrieval, and "; source update date
unknown" when there is none. Age is measured against `retrieved_at`, not the wall clock, so a
frozen snapshot gives identical labels in every run. Wording to confirm with Deepti.

**Maps handoff.** A secondary source states Google Maps URLs open up to 3 waypoints on mobile
and up to 9 otherwise (Google's own page could not be fetched; re-check before the demo).
Drivers use phones, so `handoff.max_waypoints = 3` in configs A/B/C. Longer itineraries are split
into consecutive URLs: every stop exactly once, segment k's destination = segment k+1's origin
(`evaluation/metrics/handoff.check_urls` passes on the result). Coordinates, not names.

## Consequences
- Hriday: older stations without unit data have unknown power; the configurable fallback in
  `configs/energy.yaml` (`charging_model.unknown_power_kw`) gives their duration estimate. The
  manifest reports how many such ports exist (`sources.afdc.conversion.dc_ports_with_unknown_power`
  and `validation.warnings.dc_power_unknown`), and `make snapshot-report` prints the count.
- After the test freeze, changing `unknown_power_kw` is an `energy.yaml` change: it needs an ADR
  and invalidates results that used it.
- Ian: the validator's `dc_power` check no longer fires for missing power; the new
  `dc_power_unknown` check does (test updated). Use `blocking_problems()` for freeze decisions.
- Deepti: confirm the label wording and the 365-day threshold.
- The full-US OSM step queries only available public DC-fast stations by default
  (`--osm-scope all` for every station), to keep load on the public Overpass server reasonable.
