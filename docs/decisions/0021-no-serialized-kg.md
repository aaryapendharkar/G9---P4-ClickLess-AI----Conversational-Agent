# ADR 0021: No serialized `kg.ttl` artifact (issue #191)

- Status: proposed
- Date: 2026-11-27
- Owner: Manas (retrieval / KG)
- Approvers: Aarya (source/provenance serialization), Deepti (grounded-explanation evidence ids).
  Two approvals are needed (`docs/TEAM_PLAN.md`).
- Related: #191 (this decision), ADR 0001 (rdflib + SPARQL; "KG serialized as
  `data/snapshot/kg.ttl`"), ADR 0015 (KG evidence), ADR 0019 (namespace), #115 (full-snapshot KG
  cost)

## Context
ADR 0001 planned to serialize the KG as `data/snapshot/kg.ttl`, and `retrieval/kg/build.py` kept a
TODO to write it during the snapshot build and hash it into the manifest. That never happened. In
#115 the full-snapshot graph measured 7.2 million triples, about 5.5 minutes and about 7 GB of
extra memory, so Config C builds a **route-scoped graph in memory** from the corridor candidates
instead (ADR 0001's fallback: spatial work stays outside the KG). #191 asks whether a serialized
graph is still worth producing, and to produce it only with deterministic provenance and
versioning if so.

## Who would read it
Every current KG user builds its graph in memory, and none reads a file:

| use | graph it uses | needs `kg.ttl`? |
|---|---|---|
| Config C enrichment (`retrieval/kg/enrich.py`) | route-scoped, built per request | no |
| Competency queries CQ1–CQ20 and their tests | fixture world, `build_graph(store)` | no |
| Grounded explanation and the grounding metric | `Evidence` in the saved `PlanResult` | no: evidence ids are `kg:<subject_id>:<field>` (ADR 0015 §3), with no IRI or file reference |
| Ontology adequacy metric (#158) | the ontology plus the fixture KG | no |
| Evaluation reports | saved `PlanResult`s | no |

The remaining candidate is offline inspection, such as loading the graph into a triple store or
an ontology editor. Nobody on the team has asked for that, and the full graph is a poor fit for
it, given its size.

## Measurements (2026-11-27, rdflib 7.6.0, frozen `snapshot:96dfbabb4837`)
- **Size.** An Arizona slice of the snapshot (1,659 stations, 1,797 amenities, 93,645 triples)
  serializes to 4.4 MB of Turtle, about 47 bytes per triple. At the full graph's 7.2 million
  triples that is **about 340 MB of Turtle**, more than the whole snapshot zip (17.5 MB) and too
  large to commit or open comfortably in an editor. The full graph wasn't built for this ADR: it
  needs about 7 GB of a 16 GB machine.
- **Determinism.** rdflib's Turtle output was byte-identical across Python hash seeds on that
  slice. N-Triples output was not (triple order varies).
- **Round trip.** On the fixture KG (462 triples, no blank nodes), parsing the Turtle back gives
  the same triple set, and all 20 competency-query inventory examples return the same rows (54).

So a deterministic export is technically easy. What's missing is a reader that justifies the
size, the build time and a new versioned artifact.

## Decision (proposed)
1. **No `kg.ttl` is produced, committed or released.** The snapshot keeps its current files and
   manifest (snapshot v1 is frozen, and changing it would need a new snapshot version anyway).
2. The KG stays **generated in memory** by `build_graph` (complete graph, for CQs and analysis) and
   by Config C's route-scoped enrichment. That's unchanged.
3. The stale TODO in `retrieval/kg/build.py` is replaced with a pointer to this ADR, and
   `docs/ARCHITECTURE.md` no longer lists `kg.ttl` as an output of the offline pipeline. ADR 0001
   gets a one-line pointer here, as ADR 0019 did for the namespace.
4. **One-off inspection** needs no new code: `build_graph(store).serialize(format="turtle")` gives
   deterministic Turtle for a fixture or sliced store. Such a file is local only. It isn't a
   project artifact and isn't cited as a result.

## When to revisit
Reopen this decision if a concrete reader appears, such as a published dataset, a consumer
outside this repository, or a query that must run without the snapshot. A retained artifact
should then be:
- Turtle, written by rdflib with a pinned version, and checked to be byte-identical across runs
  and hash seeds;
- a separate release asset next to the snapshot release, not a git file or a snapshot file, with
  its own SHA-256 and the source `snapshot_id` and `CC_NS` recorded in a small header or sidecar;
- covered by a round-trip test (same triples, same CQ answers, evidence ids unaffected).

## Consequences
- Nothing in Configs A/B/C, evaluation, the snapshot or saved results changes, and nothing has to
  be re-run.
- Evidence ids and grounding are unaffected, since they never referenced a serialized graph.
- Deferring the artifact costs nothing now: the measurements above say how to add it later.

## Alternatives considered
- **Full-snapshot `kg.ttl` as a release asset.** Deterministic and feasible, but about 340 MB with
  no reader, and it needs a 7 GB build in every snapshot release.
- **Fixture or route-scoped sample `kg.ttl` committed for inspection.** Small, but it would be a
  second copy of what `build_graph` already makes from the fixtures, and it could drift from the
  code without anyone noticing.
- **Keep the TODO.** Leaves ADR 0001 and the architecture document describing a file that doesn't
  exist.
