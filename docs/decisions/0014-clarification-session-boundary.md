# ADR 0014: Clarification session state and answer merge (issue #53)

- Status: accepted (2 approvals: Hriday 2026-11-14, Deepti 2026-11-14)
- Date: 2026-11-13 (accepted 2026-11-14, PR #54)
- Owner: Aarya (integration boundary, data lookups); implementation: Deepti (`nlu/`, `pipeline.py`)
- Approvers: Deepti, Hriday (integration captain from Nov 16)
- Issues: #53 (this decision), #47 (Config C clarification MVP, PR #51)

## Approvals
| Approver | Scope | Date | Record |
|---|---|---|---|
| Hriday | Planning and integration boundary: private pipeline state keyed by `request_id`, active-field-only merge, no defaults for safety-critical values, one `plan_request()` call, `run()`/harness/contracts/configs unchanged, two-failed-answer limit | 2026-11-14 | [PR #54 review](https://github.com/aaryapendharkar/G9---P4-ClickLess-AI----Conversational-Agent/pull/54#pullrequestreview-5900894151) |
| Deepti | NLU/clarification side: pure `merge_answer` boundary, no shared-contract change, unresolved safety-critical fields preserved, vehicle-option restriction and unresolved-place behaviour | 2026-11-14 | [PR #54 review](https://github.com/aaryapendharkar/G9---P4-ClickLess-AI----Conversational-Agent/pull/54#pullrequestreview-5901983151) |

## Context
Config C (`incomplete_request: clarify`) already returns `status="needs_clarification"` with a
`ClarificationQuestion`, and PR #51 makes the question choice deterministic. But `Pipeline.run`
is single-turn: there is no way to take the driver's answer and continue. Issue #53 asks where
the session state lives, how an answer maps to the requested field or option, and how to keep
unresolved safety-critical values unresolved, without changing a shared contract. Deepti's
proposal on #53 (2026-11-13): an in-memory map keyed by `request_id` in the orchestration layer,
holding the original `ParseResult` and the active `ClarificationQuestion`, and
`continue_clarification(request_id, answer) -> PlanResult` that updates only the active field or
selected option, asks the next question until complete, then calls `plan_request()` once. This
ADR confirms that design and fills in the details below.

Constraints:
- Benchmark items score only whether clarification was required (`clarification_required`,
  `missing_safety_fields`); they carry no scripted answers. The harness calls `run()` once per
  item. So the session is for the interactive demo and CLI, and `run()` must not change.
- Safety-critical fields (`origin`, `destination`, `vehicle_id`, `start_soc_pct`) are never
  guessed or defaulted, in an answer either.
- Handoff already keeps per-request state in the pipeline: `self._routes[request_id]`, which
  `approve()` reads. A second store keyed the same way is consistent with that.

## Decision

### 1. Where the state lives
In the `Pipeline` object, in memory, keyed by `request_id`, next to `self._routes`:

```python
@dataclass
class _PendingClarification:          # private to pipeline.py, not a contract
    parse: ParseResult                # the request as understood so far
    question: ClarificationQuestion   # the question the driver is answering
    failed_answers: int = 0           # unusable answers to *this* question

self._pending: dict[str, _PendingClarification] = {}
```

- Created only when `run()` returns `needs_clarification` (so only when `self.clarifier` exists,
  i.e. Config C).
- Removed when the request is planned, rejected, or runs out of attempts.
- Lives for the process only. Nothing is persisted and nothing is added to `contracts/`.
- Not in `nlu/`: parsers and the clarifier stay stateless (`parse(text, request_id)`,
  `question_for(parse)`). Not in `data/`: the data layer only answers lookups.

### 2. The interface: three pieces, no contract change

| Layer (owner) | Addition | Signature |
|---|---|---|
| pipeline (Deepti, 2 approvals) | `Pipeline.continue_clarification` | `continue_clarification(request_id: str, answer: str) -> PlanResult` |
| nlu (Deepti) | `merge_answer` in `nlu/clarification.py` | `merge_answer(parse: ParseResult, question: ClarificationQuestion, answer: str, store: DataStore) -> ParseResult` |
| data (Aarya, this PR) | `DataStore.vehicle_label`, `find_vehicles(..., among=)` | `vehicle_label(vehicle_id) -> str`; `find_vehicles(text, among: list[str] \| None = None)` |

`TripRequest`, `ParseResult`, `ClarificationQuestion` and `PlanResult` already hold everything
needed, so there is **no `CONTRACTS_VERSION` bump**.

### 3. Merge rules (`merge_answer`, a pure function)
1. Only `question.field` can change. Every other field keeps its parsed value, even if the
   answer mentions it. Returns a new `ParseResult` (`model_copy`); never mutates its input.
2. Picking an option: an answer that is an option number (`"2"`) or equals an option
   (case-insensitive, trimmed) selects that option.
3. `origin` / `destination`: `place = store.geocode(option or answer)`; accept only if
   `place.resolved`.
4. `vehicle_id`: an option number or option id selects it. Otherwise
   `store.find_vehicles(answer, among=question.options or None)`; accept only if exactly one
   vehicle comes back. With options, the answer cannot switch to a car that was not offered,
   short replies that name one option ("the RWD one", "long range") pick it, and a reply with a
   negation ("not the RWD") picks nothing, so the question is asked again.
5. `start_soc_pct`: accept exactly one explicit number 0–100 (`"40"`, `"40%"`, `"40 percent"`).
   Vague answers ("about half") are not accepted, the same as the parser.
6. Accepted: set the field and remove it from `missing_fields` and `ambiguous_fields`.
   Not accepted: return the parse unchanged (the pipeline re-asks).
7. `request_id` and `raw_text` stay as they were: `raw_text` is the driver's original message,
   which the parser metrics see. The pipeline records each merge in `result.notes` as
   `clarified <field> from answer: "<answer>"`.

### 4. `Pipeline.continue_clarification` flow
```text
pending = self._pending[request_id]            # KeyError: no open clarification for this id
merged  = merge_answer(pending.parse, pending.question, answer, self.store)
if merged.needs_input:
    if pending.question.field still missing/ambiguous in merged:
        pending.failed_answers += 1
        if pending.failed_answers >= 2:         # MAX_FAILED_ANSWERS, a constant in pipeline.py
            pop; return status="rejected_incomplete" (notes = remaining fields)
    store merged + clarifier.question_for(merged) (reset failed_answers if the field changed)
    return status="needs_clarification", same request_id
pop; return self.plan_request(merged.request, PlanResult(request_id=..., request=merged.request, ...))
```
Every returned result goes through `_finish()` so the explainer renders it, as in `run()`.

### 5. What stays the same
- `run()` and the benchmark harness: unchanged; results for Configs A/B/C do not move.
- Configs A/B: no clarifier, so nothing is ever pending; `continue_clarification()` raises `KeyError`.
- Handoff: an answer is **not** an approval. `plan_request` fills `self._routes[request_id]` as
  usual, so `approve(result, choice)` and the `ApprovalGate` audit log work unchanged for the
  same `request_id`.

### 6. What the data layer guarantees (Aarya)
- `geocode` is offline and never guesses. A place not in `places.json`, or a bare ambiguous name,
  comes back unresolved. On the snapshot the gazetteer is closed (benchmark origins and
  destinations), so in the demo an answer naming another place is re-asked, not geocoded live.
- `find_vehicles` returns `[]` (no match), one vehicle, or a tie (ambiguous). Vehicle ids are
  stable within a snapshot.
- Options stay machine values in `ambiguous_fields` (vehicle ids, place strings). To show a
  vehicle option to the driver, render `store.vehicle_label(vehicle_id)`
  ("2024 Tesla Model 3 RWD"); `TemplateExplainer` already holds the store.

### 7. Tests the implementing PR adds
- Config C: ambiguous vehicle → `continue_clarification(rid, "2")` and `continue_clarification(rid, "the RWD one")` both plan
  with the offered vehicle; an answer naming a car that was not offered is re-asked.
- Missing `start_soc_pct` → `"about half"` re-asks; a second unusable answer gives
  `rejected_incomplete`; `"40%"` plans.
- Two missing fields → two questions in `SAFETY_CRITICAL_FIELDS` order, same `request_id`.
- An answer never changes another field already parsed.
- `continue_clarification()` on an unknown id or on Config A raises `KeyError`.
- After a clarified plan, `approve()` returns handoff URLs for the same `request_id`.
- `test_walking_skeleton.py` and the harness output unchanged.

## Alternatives considered
- **Re-parse the original text plus the answer.** Simplest, but it can silently change fields
  that were already parsed, option numbers ("2") do not parse, and for the LLM parser it costs a
  call and is not deterministic.
- **Carry the pending parse in `PlanResult` and have the caller send it back** (stateless).
  Better for a future web UI, but it is a contract change (ADR + 5 approvals + version bump) and
  exposes parser internals to the UI. Revisit if the UI needs it.
- **Keep the state in the clarifier.** Makes an NLU component stateful and harder to test, and
  the clarifier would need the store and the planner.

## Consequences
- Smallest change set: one private dataclass and one method in `pipeline.py`, one pure function
  in `nlu/`, two small data lookups. No contract or config changes, no re-run of results.
- Session state is lost when the process exits. That is fine for the CLI and demo; a hosted UI
  would need the stateless alternative.
- `self._pending` and `self._routes` grow per request in a long-running process; acceptable for
  the demo. Add eviction only if a long-running UI appears.
