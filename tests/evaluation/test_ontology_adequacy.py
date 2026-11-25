"""Ontology adequacy (#158): counting rules on synthetic inventories, the harness wiring, the report."""

import subprocess
import sys
from pathlib import Path

import pytest

from evaluation.metrics.ontology_adequacy import EXPECTED_IDS, NOT_MACHINE_READABLE, ontology_adequacy

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
FORBIDDEN = ("clickless.planning", "clickless.ranking", "clickless.retrieval", "clickless.pipeline")


def inventory(**overrides):
    """CQ1..CQ20, all "tested" with a query; overrides[id] replaces/extends that entry."""
    out = []
    for qid in EXPECTED_IDS:
        entry = {"id": qid, "question": f"q {qid}", "status": "tested", "query": f"{qid.lower()}.rq",
                 "tests": ["t"], "example": {}}
        out.append({**entry, **overrides.get(qid, {})})
    return out


def rows_for(rows_by_id=None, raise_for=()):
    """run_query stub: one row unless rows_by_id says otherwise; raises for ids in raise_for."""
    rows_by_id = rows_by_id or {}

    def run(entry):
        if entry["id"] in raise_for:
            raise ValueError(f"{entry['id']}: unbound placeholder(s) ['station']")
        return rows_by_id.get(entry["id"], [("row",)])

    return run


def failing(block):
    return {f["id"]: f["reason"] for f in block["failing"]}


def test_all_pass_inventory_is_twenty_of_twenty():
    block = ontology_adequacy(inventory(), rows_for(), inventory_version=1)
    assert (block["answerable"], block["total"], block["ontology_adequacy"]) == (20, 20, 1.0)
    assert block["failing"] == [] and block["inventory_version"] == 1
    assert [q["id"] for q in block["questions"]] == list(EXPECTED_IDS)


def test_a_failing_query_is_not_answerable():
    block = ontology_adequacy(inventory(), rows_for(raise_for={"CQ3"}))
    assert block["answerable"] == 19
    assert failing(block) == {"CQ3": "query failed: ValueError: CQ3: unbound placeholder(s) ['station']"}


def test_a_query_with_no_rows_is_not_answerable():
    block = ontology_adequacy(inventory(), rows_for({"CQ12": []}))
    assert failing(block) == {"CQ12": "no rows"}


def test_an_unsupported_question_fails_with_its_reason_and_is_not_run():
    ran = []
    inv = inventory(CQ11={"status": "unsupported", "reason": "AFDC has no live status", "query": None})

    def run(entry):
        ran.append(entry["id"])
        return [("row",)]

    block = ontology_adequacy(inv, run)
    assert block["answerable"] == 19
    assert failing(block) == {"CQ11": "unsupported: AFDC has no live status"}
    assert "CQ11" not in ran


def test_tested_without_a_query_file_fails():
    block = ontology_adequacy(inventory(CQ5={"query": ""}), rows_for())
    assert failing(block) == {"CQ5": "no query file"}


def test_empty_on_fixtures_accepts_an_empty_answer_but_only_when_marked():
    inv = inventory(CQ19={"empty_on_fixtures": "every fixture station reports its power"})
    block = ontology_adequacy(inv, rows_for({"CQ19": [], "CQ6": []}))
    assert failing(block) == {"CQ6": "no rows"}  # CQ6 is not marked here
    cq19 = next(q for q in block["questions"] if q["id"] == "CQ19")
    assert cq19["answerable"] and cq19["n_rows"] == 0 and cq19["note"].startswith("empty on fixtures")


def test_empty_on_fixtures_does_not_excuse_a_failing_query():
    inv = inventory(CQ19={"empty_on_fixtures": "x"})
    assert failing(ontology_adequacy(inv, rows_for(raise_for={"CQ19"})))["CQ19"].startswith("query failed")


def test_free_text_prerequisite_is_skipped_as_not_machine_readable():
    inv = inventory(CQ9={"example_note": "needs an explanation added with build.add_explanation(graph, result) first"})
    block = ontology_adequacy(inv, rows_for(raise_for={"CQ9"}))  # would fail if it were run
    assert failing(block)["CQ9"].startswith(f"{NOT_MACHINE_READABLE}: needs an explanation")


def test_missing_and_extra_ids_cannot_raise_the_share():
    inv = [e for e in inventory() if e["id"] != "CQ20"] + [{"id": "CQ21", "status": "tested", "query": "x.rq"}]
    block = ontology_adequacy(inv, rows_for())
    assert (block["answerable"], block["total"]) == (19, 20)
    assert failing(block) == {"CQ20": "missing from inventory"} and block["ignored_ids"] == ["CQ21"]


def test_metric_module_imports_no_system_code():
    code = (
        f"import sys; sys.path.insert(0, {str(SRC)!r})\n"
        "import evaluation.metrics.ontology_adequacy\n"
        f"print('\\n'.join(sorted(m for m in sys.modules if m.startswith({FORBIDDEN!r}))))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True).stdout
    assert out.strip() == ""


# --------------------------------------------------------------------------- harness wiring


@pytest.mark.parametrize("config", ["A", "B", "C-noKG"])
def test_configs_without_the_kg_are_not_applicable(config):
    from clickless.config import load_config
    from evaluation.config_names import config_path
    from evaluation.harness import kg_ontology_adequacy

    block = kg_ontology_adequacy(load_config(str(config_path(config))))
    assert set(block) == {"not_applicable"}


def test_config_c_on_the_real_inventory():
    pytest.importorskip("rdflib")
    from clickless.config import load_config
    from evaluation.config_names import config_path
    from evaluation.harness import kg_ontology_adequacy

    block = kg_ontology_adequacy(load_config(str(config_path("C"))))
    assert block["total"] == 20 and block["inventory_version"] == 1
    assert block["kg"].startswith("fixtures")
    # CQ9's prerequisite (an explanation in the graph) is free text in the inventory; everything else
    # answers on the fixture KG (CQ6 and CQ19 are marked empty_on_fixtures).
    assert set(failing(block)) == {"CQ9"}
    assert block["answerable"] == 19


# --------------------------------------------------------------------------- report


def test_report_section_and_csv(tmp_path):
    from evaluation.report import ontology_row

    block = ontology_adequacy(inventory(CQ9={"example_note": "needs an explanation"}), rows_for(),
                              inventory_version=1)
    rows = [
        ontology_row("A", {"not_applicable": "config A does not build the KG"}),
        ontology_row("B", None),
        ontology_row("C", {**block, "kg": "fixtures (data/fixtures)"}),
        ontology_row("C-noKG", {"error": "ImportError: no rdflib"}),
    ]
    assert [r["status"] for r in rows] == [
        "n/a: config A does not build the KG",
        "n/a: no ontology_adequacy block",
        "ok",
        "n/a: ImportError: no rdflib",
    ]
    c = rows[2]
    assert (c["answerable"], c["total"], c["inventory_version"], c["failing_ids"]) == (19, 20, 1, "CQ9")
    assert c["failing_reasons"] == f"CQ9: {NOT_MACHINE_READABLE}: needs an explanation"
