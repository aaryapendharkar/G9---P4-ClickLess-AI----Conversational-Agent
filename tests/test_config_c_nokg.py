"""The "C without KG" ablation is Configuration C with only the retriever changed."""

from __future__ import annotations

import copy

from clickless.config import load_config
from clickless.pipeline import build_pipeline


def test_nokg_ablation_loads_and_differs_from_c_only_in_the_retriever():
    c = load_config("C")
    nokg = load_config("configs/config_c_nokg.yaml")
    assert c["components"]["retriever"] == "corridor+kg"
    assert nokg["components"]["retriever"] == "corridor"
    # Everything else, includes resolved, is identical: put C's retriever back and compare.
    restored = copy.deepcopy(nokg)
    restored["components"]["retriever"] = c["components"]["retriever"]
    assert restored == c


def test_nokg_ablation_builds_c_without_the_kg_enricher(store):
    c = build_pipeline(load_config("C"), store)
    nokg = build_pipeline(load_config("configs/config_c_nokg.yaml"), store)
    assert c.enricher is not None
    assert nokg.enricher is None
    assert nokg.name == c.name
    for stage in ("parser", "retriever", "ranker", "selector", "explainer"):
        assert type(getattr(nokg, stage)) is type(getattr(c, stage)), stage
