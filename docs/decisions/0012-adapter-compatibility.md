# ADR 0012: Connector compatibility with documented adapters

- Status: proposed — needs the approvals listed under Approvals before merge
- Date: 2026-11-07 (approvals section added 2026-11-08, PR #39 review)
- Owner: Manas (learning swap in `planning/filters.py`)
- Approvers: Hriday (planning owner), Aarya (vehicle table), Deepti (parser fills `connectors_override`)

## Context
`compatible_ports` accepted native connectors only, so documented adapters in the vehicle table
(ADR 0009) never reached planning (issue #37). It is the single compatibility rule for the hard
filter, the planner and the verifier, and `configs/relaxation.yaml` never relaxes
`connector_compatibility`. So a mistake here proposes a stop the driver cannot use. Three
questions were open:

1. Which adapters count? The issue requires that undocumented adapters are never inferred.
2. Does an adapter count at every charging level? `Vehicle.adapters` records connectors, not
   levels. All 14 documented adapters in `data/curated/vehicles.csv` are cited to DC
   fast-charging sources: Tesla's CCS1 adapter for the 3 Teslas, and NACS adapters for
   Supercharger access for the other 11.
   AFDC also produces NACS ports at Level 2 (Tesla destination chargers, ADR 0010).
3. How does `TripRequest.connectors_override` interact with the vehicle table? No parser fills
   it yet, but the LLM parser already extracts a `connectors` field.

## Decision
A port is compatible when:
- its connector is one of `vehicle.native_connectors` (any level), or
- its connector is one of `vehicle.adapters` **and** the port is DC fast.

Nothing else is inferred. That excludes adapters that are sold for the car but missing from the
table, and AC use of a DC adapter (a CCS1 car with a NACS DC adapter at a Tesla destination
charger).

`connectors_override` can only **narrow** the vehicle's compatible set (intersection), never
widen it. "I left my adapter at home" narrows to the native connector. A stated connector the
vehicle table does not support is ignored, so a parser or LLM mistake cannot unlock an unusable
station. `None` and `[]` both mean no override.

`compatible_ports` returns native ports before adapter ports. The planner keeps the first port of
maximal power, so a tie goes to the native plug.

## Consequences
- More stations become feasible for 14 of 15 curated vehicles. On the fixtures, the Comet at 25%
  becomes feasible through the CCS1-only Anthem station
  (`tests/integration/test_adapter_feasibility.py`). Planning results produced before this change
  are out of date.
- A documented AC adapter (e.g. a J1772 adapter) would be ignored until `Vehicle` records the
  level an adapter supports. That needs a contract change; it is conservative until then.
- Adapter power limits are not modelled: charging time still uses min(vehicle max DC, port power).
- The KG competency queries CQ1/CQ2 (`cc:supports|cc:supportsViaAdapter`) ignore levels, so they
  can list a NACS L2 port that planning rejects. The filter is authoritative for planning.
- When Deepti grounds the LLM `connectors` field into `connectors_override`, this rule already
  keeps it safe. The explanation does not yet say that a stop needs the adapter; that is a
  follow-up for `explain/`.
- Shared planning behavior changes (the filter, the planner and the verifier), so this ADR requires
  two approvals.

## Approvals
Each approver records approval here (name, date, PR comment or sync) before the status changes to
accepted. Merge needs both required approvals.

| Approver | Decision they approve | Required | Status |
|---|---|---|---|
| Hriday (planning owner) | Adapters count only at DC fast ports; native ports come first; the filter, planner and verifier keep sharing `compatible_ports` | yes | pending |
| Aarya (vehicle table, ADR 0009) | The DC-fast-only rule loses no documented adapter: all 14 adapters in `data/curated/vehicles.csv` are cited to DC fast-charging sources, and any AC adapter added later waits for a contract field | yes | pending |
| Deepti (parser) | `connectors_override` only narrows the vehicle's compatible set, so a grounded LLM `connectors` value can never widen it | for the override rule | pending |
