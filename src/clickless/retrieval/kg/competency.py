"""Load and run the competency-question SPARQL files (docs/COMPETENCY_QUESTIONS.md).

Each file marks a value the caller must supply with `VALUES ?x { cc:x_REPLACE_ME }`. This module
removes that line and binds `?x` with rdflib's initBindings instead, so every caller (tests,
notebooks, the report) runs the same query text. A placeholder left unbound is an error: run as
it is, the query would match `cc:x_REPLACE_ME` and quietly return no rows.
"""

from __future__ import annotations

import re
from pathlib import Path

CQ_DIR = Path(__file__).parent / "competency_queries"
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

    Values that are not RDF terms (a datetime for CQ6's ?cutoff, say) are wrapped in a Literal."""
    from rdflib import Literal
    from rdflib.plugins.sparql import prepareQuery
    from rdflib.term import Node

    query = prepareQuery(competency_query_text(name, *bindings))
    terms = {var: value if isinstance(value, Node) else Literal(value) for var, value in bindings.items()}
    return list(graph.query(query, initBindings=terms))
