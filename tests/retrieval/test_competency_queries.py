"""Competency-question SPARQL files (CQ2–CQ6 drafted by Aarya; see docs/COMPETENCY_QUESTIONS.md)."""

import re

import pytest

from clickless.config import repo_path

CQ_DIR = repo_path("src/clickless/retrieval/kg/competency_queries")
FILES = sorted(CQ_DIR.glob("cq*.rq"))


def _body(path):
    return "\n".join(line for line in path.read_text().splitlines() if not line.lstrip().startswith("#"))


def test_cq1_to_cq6_exist():
    numbers = {int(re.match(r"cq(\d+)_", p.name).group(1)) for p in FILES}
    assert {1, 2, 3, 4, 5, 6} <= numbers


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_query_structure(path):
    body = _body(path)
    assert re.search(r"PREFIX\s+cc:\s+<https://example\.org/clickless#>", body)
    assert re.search(r"\bSELECT\b", body) and re.search(r"\bWHERE\b", body)
    assert body.count("{") == body.count("}") and body.count("(") == body.count(")")
    for prefix in set(re.findall(r"\b([a-z]+):[A-Za-z_]", body)) - {"https", "http"}:
        assert re.search(rf"PREFIX\s+{prefix}:", body), f"{path.name}: prefix {prefix}: not declared"


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_query_parses_with_rdflib(path):
    sparql = pytest.importorskip("rdflib.plugins.sparql")
    sparql.prepareQuery(path.read_text())
