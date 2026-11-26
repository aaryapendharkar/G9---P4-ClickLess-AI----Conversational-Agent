"""Load and run the competency-question SPARQL files (docs/COMPETENCY_QUESTIONS.md).

Each file marks a value the caller must supply with `VALUES ?x { cc:x_REPLACE_ME }`. This module
removes that line and binds `?x` with rdflib's initBindings instead, so every caller (tests,
notebooks, the report) runs the same query text. A placeholder left unbound is an error: run as
it is, the query would match `cc:x_REPLACE_ME` and quietly return no rows.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

CQ_DIR = Path(__file__).parent / "competency_queries"
INVENTORY = CQ_DIR / "inventory.json"
PLACEHOLDER = "REPLACE_ME"


def competency_query_path(name: str) -> Path:
    """Path of a CQ file, by file name or prefix: 'cq06', 'cq06_stale_station_records' or with '.rq'."""
    stem = name.removesuffix(".rq")
    matches = sorted(p for p in CQ_DIR.glob("cq*.rq") if p.stem == stem or p.stem.startswith(f"{stem}_"))
    if len(matches) != 1:
        raise ValueError(f"no single competency query named {name!r} in {CQ_DIR} (found {len(matches)})")
    return matches[0]


def competency_query_text(name: str, *bound: str) -> str:
    """Query text with the placeholder line of each variable in `bound` removed.

    Raises ValueError if a variable has no placeholder, or if any placeholder is left."""
    text = competency_query_path(name).read_text(encoding="utf-8")
    for var in bound:
        text, n = re.subn(rf"VALUES \?{var} \{{ cc:{var}_{PLACEHOLDER} \}}", "", text)
        if n != 1:
            raise ValueError(f"{name}: no VALUES placeholder for ?{var}")
    if PLACEHOLDER in text:
        left = sorted(set(re.findall(rf"VALUES \?(\w+) \{{ cc:\w+_{PLACEHOLDER} \}}", text)))
        raise ValueError(f"{name}: unbound placeholder(s) {left}; pass them as keyword arguments")
    return text


def run_competency_query(graph, name: str, **bindings) -> list:
    """Rows of CQ `name` on `graph`, each keyword binding the variable of the same name.

    Values that are not RDF terms (a datetime for CQ6's ?cutoff, say) are wrapped in a Literal. A
    float becomes an exact xsd:decimal, as the graph stores numbers (build._decimal): as xsd:double,
    7.2 is 7.2000000000000002, and "at least 7.2 kW" would miss a 7.2 kW port."""
    from rdflib import Literal
    from rdflib.plugins.sparql import prepareQuery
    from rdflib.term import Node

    def term(value):
        if isinstance(value, Node):
            return value
        if isinstance(value, float):
            return Literal(Decimal(repr(value)))
        return Literal(value)

    query = prepareQuery(competency_query_text(name, *bindings))
    terms = {var: term(value) for var, value in bindings.items()}
    return list(graph.query(query, initBindings=terms))


def load_inventory() -> list[dict]:
    """The competency-question inventory (CQ1-CQ20, inventory.json): id, question, status
    ("tested", or "unsupported" with a reason), query file, example bindings, tests. The ontology
    adequacy metric counts it (#158); tests/retrieval/test_competency_inventory.py keeps it honest."""
    return json.loads(INVENTORY.read_text(encoding="utf-8"))["questions"]


def example_bindings(entry: dict) -> dict:
    """An inventory entry's example bindings as run_competency_query keywords: "cc:x" -> URI,
    {"datetime": iso} -> datetime, anything else unchanged."""
    from datetime import datetime

    from rdflib import URIRef

    from .build import CC_NS

    def value(v):
        if isinstance(v, str) and v.startswith("cc:"):
            return URIRef(CC_NS + v[3:])
        if isinstance(v, dict) and "datetime" in v:
            return datetime.fromisoformat(v["datetime"])
        return v

    return {k: value(v) for k, v in (entry.get("example") or {}).items()}


def run_inventory_example(graph, entry: dict) -> list:
    """Run an inventory entry's example on `graph`, setting up what it needs first.

    An entry with `example_explanation` (CQ9) gets that explanation written into a copy of the graph
    with build.add_explanation (its claims and evidence as a PlanResult), so the caller's graph is
    never changed. Everything the example needs is in the inventory: no free-text prerequisite."""
    spec = entry.get("example_explanation")
    if spec:
        from rdflib import Graph

        from ...contracts import Claim, Evidence, PlanResult
        from .build import add_explanation, explanation_uri

        copy = Graph()
        copy += graph
        add_explanation(copy, PlanResult(
            request_id=spec["request_id"], config="C", status="ok",
            claims=[Claim.model_validate(c) for c in spec["claims"]],
            evidence=[Evidence.model_validate(e) for e in spec.get("evidence", [])],
        ))
        bindings = {"explanation": explanation_uri(spec["request_id"]), **example_bindings(entry)}
        return run_competency_query(copy, entry["query"], **bindings)
    return run_competency_query(graph, entry["query"], **example_bindings(entry))
