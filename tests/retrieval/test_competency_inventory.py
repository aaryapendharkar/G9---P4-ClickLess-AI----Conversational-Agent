"""The competency-question inventory is complete and honest (#158).

The ontology-adequacy metric counts inventory.json, so nothing may be counted as answerable unless
it has a query file, runs, returns rows on the fixture KG and has expected-answer tests.
"""

import ast
import re

import pytest

from clickless.config import repo_path
from clickless.retrieval.kg.build import build_graph
from clickless.retrieval.kg.competency import CQ_DIR, load_inventory, run_inventory_example

pytest.importorskip("rdflib")

INVENTORY = load_inventory()


def test_twenty_distinct_questions_in_order():
    assert [e["id"] for e in INVENTORY] == [f"CQ{n}" for n in range(1, 21)]
    questions = [e["question"].strip().lower() for e in INVENTORY]
    assert all(questions) and len(set(questions)) == 20


def test_every_query_file_is_in_the_inventory_exactly_once():
    files = sorted(p.name for p in CQ_DIR.glob("cq*.rq"))
    listed = sorted(e["query"] for e in INVENTORY if e.get("query"))
    assert listed == files
    for e in INVENTORY:
        if e.get("query"):
            number = int(re.match(r"cq(\d+)_", e["query"]).group(1))
            assert f"CQ{number}" == e["id"]


def test_status_is_explicit():
    for e in INVENTORY:
        assert e["status"] in {"tested", "unsupported"}, e["id"]
        if e["status"] == "unsupported":
            assert e.get("reason"), f"{e['id']}: an unsupported question needs a reason"
        else:
            assert e.get("query") and e.get("tests"), e["id"]


def _defined_tests(path):
    tree = ast.parse(repo_path(path).read_text(encoding="utf-8"))
    return {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")}


def test_listed_tests_exist():
    for e in INVENTORY:
        for ref in e.get("tests", []):
            path, name = ref.split("::")
            assert name in _defined_tests(path), f"{e['id']}: {ref} not found"


@pytest.fixture(scope="module")
def graph(store):
    return build_graph(store)


@pytest.mark.parametrize("entry", [e for e in INVENTORY if e["status"] == "tested"], ids=lambda e: e["id"])
def test_each_tested_question_answers_on_the_fixture_kg(entry, graph):
    """The example returns rows: no vacuous, placeholder-only or empty answer is counted. Everything
    an example needs is in the inventory (run_inventory_example sets it up), never free text."""
    assert "example_note" not in entry, f"{entry['id']}: a free-text prerequisite can't be set up by the metric"
    rows = run_inventory_example(graph, entry)
    if entry.get("empty_on_fixtures"):  # the fixtures lack the case; its tests build it (and must say so)
        assert rows == [], f"{entry['id']}: now answers on the fixtures; drop empty_on_fixtures"
        return
    assert rows, f"{entry['id']}: example bindings returned no rows"


def test_cq9_example_explanation_is_set_up_on_a_copy(graph):
    """CQ9's example (#168): one claim backed by a station fact, one by a planning number."""
    entry = next(e for e in INVENTORY if e["id"] == "CQ9")
    size = len(graph)
    rows = run_inventory_example(graph, entry)
    assert [(str(r.text), None if r.source is None else str(r.source).rsplit("#", 1)[-1]) for r in rows] == [
        ("Stop at Fixture Camp Verde Hub.", "source_station_FIX-ST-004"),
        ("Arrive with about 22%.", None),
    ]
    assert len(graph) == size  # the caller's graph is untouched


def test_docs_table_matches_the_inventory():
    doc = repo_path("docs/COMPETENCY_QUESTIONS.md").read_text(encoding="utf-8")
    for e in INVENTORY:
        number = e["id"][2:]
        row = next((line for line in doc.splitlines() if line.startswith(f"| {number} |")), None)
        assert row, f"{e['id']} missing from docs/COMPETENCY_QUESTIONS.md"
        assert e.get("query", "") in row and e["question"] in row, e["id"]
