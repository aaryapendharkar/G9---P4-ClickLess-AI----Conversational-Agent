"""Ontology adequacy (proposal §V-C, #158): how many of the 20 competency questions the KG answers. Owner: Ian.

Input: the competency-question inventory (clickless/retrieval/kg/competency_queries/inventory.json,
#161) and a `run_query(entry) -> rows` callable that runs one entry's query with its example
bindings. The caller (evaluation.harness) supplies both; this module never imports the KG code, so
the counting rules below stay independent of the system they score.

A question counts as answerable only if ALL of these hold:
  1. its inventory status is "tested" ("unsupported" entries fail with their documented reason);
  2. it names a query file;
  3. it has no prerequisite the metric cannot set up: an `example_note` is free text (a
     machine-readable `example_explanation`, as CQ9 has, is set up by the caller's runner), so the
     question fails with "prerequisite not machine-readable";
  4. its query runs without raising;
  5. it returns at least one row, unless the inventory marks it `empty_on_fixtures` (the fixtures
     lack the case and the question's own tests build it), in which case an empty answer is accepted.
A question id expected in CQ1..CQ20 but missing from the inventory fails as "missing from inventory",
so a shorter inventory can never raise the share.

Where it runs: on the fixture KG, where the inventory's example bindings are valid (most bind
fixture entities such as cc:station_FIX-ST-004). The harness runs it once per run, only for configs
that build the KG (Config C); the others record n/a.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

EXPECTED_IDS: tuple[str, ...] = tuple(f"CQ{n}" for n in range(1, 21))
NOT_MACHINE_READABLE = "prerequisite not machine-readable"


def _verdict(entry: Mapping[str, Any], run_query: Callable[[Mapping[str, Any]], Sequence[Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"id": entry.get("id"), "status": entry.get("status"), "n_rows": None, "note": ""}
    status = entry.get("status")
    if status != "tested":
        reason = entry.get("reason") or "no reason given"
        return {**out, "answerable": False, "reason": f"{status or 'no status'}: {reason}"}
    if not entry.get("query"):
        return {**out, "answerable": False, "reason": "no query file"}
    if entry.get("example_note"):
        return {**out, "answerable": False, "reason": f"{NOT_MACHINE_READABLE}: {entry['example_note']}"}
    try:
        rows = run_query(entry)
    except Exception as e:  # noqa: BLE001 - a failing query is a result, not a crash
        return {**out, "answerable": False, "reason": f"query failed: {type(e).__name__}: {e}"}
    out["n_rows"] = len(rows)
    if rows:
        return {**out, "answerable": True, "reason": ""}
    if entry.get("empty_on_fixtures"):
        return {**out, "answerable": True, "reason": "", "note": f"empty on fixtures: {entry['empty_on_fixtures']}"}
    return {**out, "answerable": False, "reason": "no rows"}


def ontology_adequacy(
    questions: Sequence[Mapping[str, Any]],
    run_query: Callable[[Mapping[str, Any]], Sequence[Any]],
    *,
    inventory_version: Any = None,
    expected_ids: Sequence[str] = EXPECTED_IDS,
) -> dict[str, Any]:
    """answerable / len(expected_ids), the failing question ids with reasons, and every verdict."""
    by_id = {e.get("id"): e for e in questions}
    per_question = []
    for qid in expected_ids:
        entry = by_id.get(qid)
        if entry is None:
            per_question.append({"id": qid, "status": None, "answerable": False, "n_rows": None,
                                 "reason": "missing from inventory", "note": ""})
        else:
            per_question.append(_verdict(entry, run_query))
    extra = sorted(str(i) for i in by_id if i not in expected_ids)
    answerable = sum(v["answerable"] for v in per_question)
    total = len(expected_ids)
    return {
        "answerable": answerable,
        "total": total,
        "ontology_adequacy": answerable / total if total else None,
        "inventory_version": inventory_version,
        "failing": [{"id": v["id"], "reason": v["reason"]} for v in per_question if not v["answerable"]],
        "ignored_ids": extra,
        "questions": per_question,
    }
