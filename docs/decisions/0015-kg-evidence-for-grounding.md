# ADR 0015: KG evidence for Config C grounded explanations (issue #52)

- Status: proposed — needs the approvals listed under Approvals
- Date: 2026-11-14
- Owner: Manas (retrieval / KG)
- Approvers: Deepti (explainer reads the evidence), Hriday (`PlanResult` owner; integration captain
  from Nov 16), Ian (grounding metric resolves the ids)
- Issues: #52 (this decision), #47 (Config C), PR #58 (Config C clarification reaches planning)

## Context
`Candidate.evidence: list[Evidence]` has existed since contracts v0.1 ("filled by KG enrichment
(Config C)"), but `KGEnricher.enrich` raised `NotImplementedError`. So Config C could not plan
at all: Hriday's PR #58 review found that the clarified request reaches planning and stops in
the stub. `explain/grounded.py` therefore cites only `verified:<request_id>:...` ids for numbers
computed by planning, and its docstring notes that `PlanResult` carries no KG evidence.
Issue #52 asks for: how `Candidate.evidence` is populated, the ids, values and provenance the
explainer needs, the boundary `grounded.py` consumes, and a fixture-level test.

Constraints: modules talk only through contracts (ARCHITECTURE rule 1); numbers shown to the
driver come from contract fields (rule 4); unknown power stays unknown (ADR 0010); a missing
wheelchair tag is unknown and never accessible (proposal §II, ADR 0011); "C without KG" must
remain a one-line ablation.

## Decision

### 1. Producer (this PR, `retrieval/` only, no contract change)
`KGEnricher.enrich(candidates, request)` appends `Evidence` to each `Candidate.evidence`. The facts
are read back from an rdflib graph (ADR 0001) that `retrieval/kg/build.py::build_graph(store)`
builds from the DataStore on the first `enrich` call (ontology v0.2 in `ontology.ttl`). Enrichment
never adds or removes candidates, and never changes amenities, distances or match scores.

### 2. Format: one `Evidence` per atomic fact

| `subject_id` | `field` | `value` | `provenance` |
|---|---|---|---|
| station id | `name`, `network`, `status`, `access` | str (`network` may be None) | the station record (AFDC) |
| station id | `ports[i].connector`, `ports[i].level` | `"CCS1"`, `"dc_fast"` | the station record |
| station id | `ports[i].power_kw` | float, or **None** = not reported | the station record |
| station id | `ports[i].count` | int | the station record |
| station id | `near[<amenity_id>].distance_m` | float, metres (straight line) | **DERIVED** (see 4) |
| amenity id | `name`, `category`, `opening_hours` | str or None | the amenity record (OSM) |
| amenity id | `wheelchair` | `"yes"` \| `"limited"` \| `"no"` \| `"unknown"` | the amenity record |

- Each candidate gets its station's facts (ports by index) and, for every entry of
  `candidate.amenities` in order, the distance and then that amenity's facts.
- Every field in the table is always present. `None` means "the source did not report it". Say it
  is unknown; never show it as 0 or a default. `wheelchair: "unknown"` never counts as accessible.
- Values equal the DataStore record field for field (enum values as their strings), and the
  distance is exactly `NearbyAmenity.distance_m`. `tests/retrieval/test_kg_enrichment.py` checks
  every fact.

### 3. Ids
`evidence_id = "kg:<subject_id>:<field>"`, e.g. `kg:FIX-ST-004:ports[0].power_kw`,
`kg:osm:node/42#toilets:wheelchair`. A KG fact does not depend on the request, so its id is the
same in every request, configuration and run on the same snapshot. Treat the id as opaque: read
`subject_id` and `field` instead of parsing it. The explainer's own `verified:<request_id>:...`
ids, for numbers computed by planning, stay as they are.

### 4. Provenance
The record's own `Provenance` (source, record id, URL, `retrieved_at`, `source_updated_at`,
status), unchanged, so `data/provenance.py::status_label(evidence.provenance)` gives the
data-status label directly. A distance is computed by us, so it is `source="DERIVED"`,
`source_record_id="haversine(<station_id>,<amenity_id>)"`, `retrieved_at` = the later of the two
records, status = the less static of the two (runtime > cached > static).

### 5. Boundary to the explainer: `PlanResult.evidence` (needs approval: contracts v0.5)
`PlanResult` does not carry candidates, so `Candidate.evidence` cannot reach `explain()` today.
Proposal: an additive, optional contract field, filled by the pipeline in a follow-up PR after
this ADR is approved and PR #58 has merged (both touch `pipeline.py`):

```python
class PlanResult(BaseModel):
    ...
    evidence: list[Evidence] = Field(default_factory=list)  # v0.5: KG facts about the shown plans (ADR 0015)
```

- **Pipeline:** after selection, for each shown itinerary (alternatives, then relaxation
  alternatives), for each stop: the station's facts, then for each `stop.amenity_ids` entry the
  distance and the amenity's facts. Each fact is included once, by `evidence_id`, in that order.
  Only facts about what the driver is shown, not all candidates (on the snapshot that would be
  thousands per result).
- **Explainer (Deepti):** a claim about a KG fact lists that fact's `evidence_id` in
  `Claim.supported_by`, e.g. "Stop at Fixture Camp Verde Hub (CCS1, 50 kW)" →
  `["kg:FIX-ST-004:name", "kg:FIX-ST-004:ports[0].connector", "kg:FIX-ST-004:ports[0].power_kw"]`.
  With `evidence == []` (Configs A/B, or the C-without-KG ablation) it keeps today's
  verified-only claims.
- **Grounding metric (Ian):** `evidence_lookup = {e.evidence_id: e for e in result.evidence}`; a
  claim that cites a `kg:` id missing from it is unsupported. Saved result JSON carries the
  evidence, so the metric runs offline.
- Configs A and B have no enricher, so their `PlanResult.evidence` is empty and no reported number
  moves.

### 6. Not in this decision
- Vehicle facts (connectors, max DC power, per-field provenance) as evidence: follow-up. The graph
  already holds `cc:supports` / `cc:supportsViaAdapter`.
- `WeightedRanker.evidence_coverage` stays 0.0 (Hriday + Aarya TODO); ranking does not change.
- Writing `data/snapshot/kg.ttl` in the offline build and hashing it into the manifest; measuring
  the in-memory build on the full snapshot (ADR 0001 fallback: keep spatial work outside the KG).

## Alternatives considered
- **The explainer re-reads the DataStore by deterministic ids** (no contract change). It would
  re-implement retrieval inside `explain/`, the C-without-KG ablation could not switch it off,
  and saved results could not be checked by the metric.
- **Keep the evidence in the pipeline (like `_routes`) and inject a lookup into the explainer.**
  No contract change, but the saved `PlanResult` lacks the evidence, so grounding can only be
  measured inside the process. It also changes the explainer's constructor.
- **Request-scoped ids** (`kg:<request_id>:...`). KG facts do not depend on the request, and
  stable ids let one lookup serve every result and make results comparable across runs.
- **Emit a fact only when the value is known.** Then "power not reported" or "accessibility
  unknown" could not be cited, and the explainer would have to infer absence.

## Consequences
- Config C plans end to end on the fixtures (with TF-IDF swapped in for Sentence-BERT in the test,
  since CI has no `nlp` extra). The clarified request from PR #58 now reaches a plan instead of the
  stub.
- CI installs the `kg` extra (rdflib, pure Python), so the KG tests and the six competency-query
  parse tests run in CI instead of being skipped.
- Fixture graph: 436 triples, about 20 ms to build. The full snapshot is not measured yet.
- When the field lands: `CONTRACTS_VERSION` 0.4.0 → 0.5.0, an additive change, so nothing has to be
  re-run.

## Approvals
Each approver records approval here (name, date, PR comment or sync) before the status changes to
accepted.

| Approver | Decision they approve | Required | Status |
|---|---|---|---|
| Deepti (explainer) | Sections 2–4 are the facts `GroundedLLMExplainer` cites; claims list `kg:` ids from `PlanResult.evidence`; empty evidence keeps verified-only claims | yes | pending |
| Hriday (`PlanResult`, integration) | Optional `PlanResult.evidence` (v0.5), filled by the pipeline with facts about shown stops only (section 5) | yes | pending |
| Ian (grounding metric) | `claim_support` resolves `kg:` ids through `result.evidence` | for section 5 | pending |
