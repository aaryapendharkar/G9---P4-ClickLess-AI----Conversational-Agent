# ADR 0009: Third-party sources in the curated vehicle table

- Status: accepted (2 approvals: Hriday 2026-11-04, Ian 2026-11-09)
- Date: 2026-11-02 (revised 2026-11-03 for issue #28; accepted 2026-11-13)
- Owner: Aarya
- Approvers: Ian (benchmark/oracle), Hriday (energy model)

## Approvals
| Approver | Scope | Date | Record |
|---|---|---|---|
| Hriday | Energy model: source policy, usable-only capacity, strict loading, `THIRD_PARTY` + `CONTRACTS_VERSION` 0.4.0. Not a certification of every CSV value. | 2026-11-04 | [issue #28 comment](https://github.com/aaryapendharkar/G9---P4-ClickLess-AI----Conversational-Agent/issues/28#issuecomment-6620754260) |
| Ian | Benchmark/evaluation, including the LEAF resolution (46 kW cap; LEAF excluded from charge-time accuracy and charge-time sensitivity results) | 2026-11-09 | [issue #28 comment](https://github.com/aaryapendharkar/G9---P4-ClickLess-AI----Conversational-Agent/issues/28#issuecomment-6735232222) |

The contract change shipped with the loader in PR #29. The pre-freeze data follow-ups below
remain open and are tracked in issue #36 (final snapshot criteria).

## Context
Proposal §II says EPA data give efficiency and *manufacturer specifications* give usable capacity,
connectors/adapters and maximum DC power. Building `data/curated/vehicles.csv` (15 variants)
showed that manufacturers often do not publish these values in the form we need:

- Tesla publishes no battery capacity at all.
- Hyundai, Kia, Chevrolet (Bolt EUV, Equinox EV) and BMW publish no usable capacity, or only a
  total/nominal figure (e.g. Kia "Battery Energy 77.4 kWh"). Using a total figure overstates
  the energy the planner may spend, which is the unsafe direction.
- Several manufacturers give only a charger rating ("350 kW charger") rather than the vehicle's
  peak DC acceptance.
- VW and BMW announced NACS-adapter support through press coverage, not a spec page we can cite.

`contracts/provenance.py` only allows `MANUFACTURER` and `FUELECONOMY` for curated values.

## Decision
1. **Keep the researched values.** Use the manufacturer's figure when it states the quantity the
   column means (usable kWh, vehicle peak DC kW, a documented adapter). Otherwise use a
   reputable third-party spec source (EVKX, InsideEVs, ArenaEV, Cars.com, EVSpecs, Electrive,
   TeslaBatteryCheck) that states that quantity explicitly. Efficiency always comes from the
   EPA combined MPGe on fueleconomy.gov: `kWh/km = 33.705 / MPGe / 1.609344` (wall-to-wheels,
   so it includes charging losses and errs on the safe side).
2. **Record the source type per value.** `vehicles.csv` gets `usable_kwh_source_type`,
   `efficiency_source_type`, `adapter_source_type` and `max_dc_source_type`, each one of
   `MANUFACTURER`, `FUELECONOMY` or `THIRD_PARTY`. The type follows from the URL's publisher
   (manufacturer domain, fueleconomy.gov, anything else), never chosen by hand.
3. **Provenance mapping in `data/vehicles.py`.** Each source type maps 1:1 to the `SourceName`
   of the same name. There is no interim `DERIVED` mapping: the contract change ships in the
   same PR as the loader, so the loader never runs against v0.3.
4. **Contract change.** Add `"THIRD_PARTY"` to `SourceName` and bump `CONTRACTS_VERSION`
   0.3.0 → 0.4.0. Adding a literal value is backward compatible; `tests/test_contracts.py`
   checks every v0.3 source label still validates and unknown labels are rejected.
5. **Sanity check kept with the data.** For every row, usable kWh ÷ (EPA range × efficiency) must
   fall in 0.80–0.92 (expected charging-loss band). All 15 rows pass (0.83–0.90).
6. **Usable capacity only (issue #28).** A `usable_kwh_basis` column states, per row, that
   `usable_kwh` is usable (net) capacity. The loader accepts only `usable`; a nominal/gross
   figure (e.g. a manufacturer's "60 kWh battery") must be replaced by a sourced usable figure
   before it can load. It is never derated by a guessed percentage.
7. **Loader is strict (issue #28).** `load_curated_vehicles` rejects, with file:line and column:
   a changed header, wrong field count, duplicate `vehicle_id`, unknown connector, unknown source
   type, non-https URL, a FUELECONOMY URL not on fueleconomy.gov, out-of-range numbers (unit
   mistakes), adapters without a source (or a source without adapters), and malformed aliases.
   Provenance is kept per field: `usable_kwh`, `efficiency_kwh_per_km`, `max_dc_kw`, and
   `adapters` when present (status `static`, `retrieved_at` = `accessed_on`).
8. **Alias matching (issue #28; revised after CI run #67).** `DataStore.find_vehicles` matches
   aliases as whole words and picks the vehicle whose matched aliases cover the most characters
   of the text (union of spans). "Tesla Model 3 RWD" resolves to the RWD row (the earlier
   longest-single-alias rule tied on "tesla model 3"), while "my Model 3" / "my Tesla Model 3"
   stay ambiguous (both Model 3 rows share `model 3` / `tesla model 3` on purpose, annotation
   guide rule 2). Model year in the text is not used yet.

Current split (15 vehicles): usable kWh 3 manufacturer / 12 third-party; max DC 5 / 10;
adapters 12 manufacturer / 2 third-party (1 vehicle has none); efficiency 15 FuelEconomy.

## Known caveats
- Nissan LEAF SV PLUS (resolved, issue #28): Nissan USA sells the pack as "60 kWh"; it is the
  62 kWh-nominal e+ pack with 59.0 kWh usable (ev-database.org, "Useable Capacity 59.0 kWh";
  EVKX agrees). The table now uses 59.0 kWh (THIRD_PARTY). The Nissan figure is no longer used.
- Nissan LEAF max DC (resolved, issue #28 review by Hriday, 2026-11-04): Nissan's "100 kW
  (CHAdeMO)" is the quick-charge port rating, not the car's sustained acceptance. The table now
  uses a documented conservative cap of **46 kW** ("Charge Power (max) 46 kW DC",
  ev-database.org, same page as the usable capacity; THIRD_PARTY). Even with the cap the
  planner's constant-power-to-80% model is optimistic for this car: EVKX reports a 10-80%
  average of about 27 kW, and the pack has no active cooling, so repeated fast charges slow down
  further ("rapidgate"). **The LEAF is therefore excluded from charging-time accuracy claims**;
  its feasibility results (energy, connectors, reserve) are unaffected and stay in scope.
- Native connectors have no per-value source column (they are not energy fields); add one if
  the grounding metric needs to cite them.
- F-150 Lightning ER peak DC (150 kW) is from Ford's 2025 spec sheet; the 2024 truck uses the same
  131 kWh battery.
- Tesla CCS1 adapter compatibility is cited to Tesla's product page, which blocked automated
  access on 2026-11-02; verify by hand before the snapshot freeze.

## Pre-freeze data follow-ups (keep until verified)
1. Tesla CCS1 adapter compatibility (`tesla-model-3-*`, `tesla-model-y-*`): open Tesla's product
   page by hand and confirm the 2024 Model 3/Y are listed; record the result here.
2. F-150 Lightning ER max DC: replace the 2025 spec-sheet source with a 2024 one if Ford
   publishes it, or keep this caveat.
3. LEAF: excluded from charging-time accuracy claims (above); evaluation reports should say so.
   Ian's evaluation keeps LEAF items (currently aarya-011, aarya-027, and deepti-010 once PR #40
   merges) in feasibility metrics and excludes them from charge-time accuracy and charge-time
   sensitivity results.

## Consequences
- The final report states: *"Efficiency comes from EPA ratings; usable capacity, peak DC power
  and adapters come from manufacturer specifications where published, otherwise from named
  third-party spec databases, and every value's source type is recorded in the vehicle table."*
- Provenance-completeness and grounding metrics can report the share of third-party facts.
- Values are static and frozen with snapshot v1; any later correction is a new snapshot + ADR.
