# ADR 0009: Third-party sources in the curated vehicle table

- Status: proposed
- Date: 2026-11-02
- Owner: Aarya
- Approvers: Ian (benchmark/oracle), Hriday (energy model)

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
3. **Provenance mapping in `data/vehicles.py`.** `MANUFACTURER` and `FUELECONOMY` map to the
   same `SourceName`. `THIRD_PARTY` maps to a new `SourceName` value `"THIRD_PARTY"` once the
   contract change below is approved; until then the loader uses `"DERIVED"` and always sets
   `source_url`, so the real publisher is never lost.
4. **Contract change (separate approval).** Add `"THIRD_PARTY"` to `SourceName` and bump
   `CONTRACTS_VERSION` 0.3.0 → 0.4.0. Adding a literal value is backward compatible.
5. **Sanity check kept with the data.** For every row, usable kWh ÷ (EPA range × efficiency) must
   fall in 0.80–0.92 (expected charging-loss band). All 15 rows pass (0.83–0.92).

Current split (15 vehicles): usable kWh 4 manufacturer / 11 third-party; max DC 6 / 9;
adapters 12 manufacturer / 2 third-party (1 vehicle has none); efficiency 15 FuelEconomy.

## Known caveats
- Nissan LEAF SV PLUS: 60 kWh is Nissan's stated (nominal) capacity; usable is likely a few kWh
  lower. Ratio check 0.92 is the highest in the table, consistent with this.
- F-150 Lightning ER peak DC (150 kW) is from Ford's 2025 spec sheet; the 2024 truck uses the same
  131 kWh battery.
- Tesla CCS1 adapter compatibility is cited to Tesla's product page, which blocked automated
  access on 2026-11-02; verify by hand before the snapshot freeze.

## Consequences
- The final report states: *"Efficiency comes from EPA ratings; usable capacity, peak DC power
  and adapters come from manufacturer specifications where published, otherwise from named
  third-party spec databases, and every value's source type is recorded in the vehicle table."*
- Provenance-completeness and grounding metrics can report the share of third-party facts.
- Values are static and frozen with snapshot v1; any later correction is a new snapshot + ADR.
