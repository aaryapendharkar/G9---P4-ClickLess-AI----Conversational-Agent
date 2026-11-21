"""The "C without KG" ablation is Configuration C with only the retriever changed."""

from __future__ import annotations

import copy

from clickless.config import load_config
from clickless.pipeline import build_pipeline


def test_nokg_ablation_loads_and_differs_from_c_only_in_name_and_retriever():
    c = load_config("C")
    nokg = load_config("configs/config_c_nokg.yaml")
    assert c["components"]["retriever"] == "corridor+kg"
    assert nokg["components"]["retriever"] == "corridor"
    assert (c["name"], nokg["name"]) == ("C", "C-noKG")  # its own results files and report column
    # Everything else, includes resolved, is identical: put C's name and retriever back and compare.
    restored = copy.deepcopy(nokg)
    restored["components"]["retriever"] = c["components"]["retriever"]
    restored["name"] = c["name"]
    assert restored == c


def test_nokg_ablation_builds_c_without_the_kg_enricher(store):
    c = build_pipeline(load_config("C"), store)
    nokg = build_pipeline(load_config("configs/config_c_nokg.yaml"), store)
    assert c.enricher is not None
    assert nokg.enricher is None
    assert (c.name, nokg.name) == ("C", "C-noKG")
    for stage in ("parser", "retriever", "ranker", "selector", "explainer"):
        assert type(getattr(nokg, stage)) is type(getattr(c, stage)), stage


def test_harness_resolves_the_ablation_by_name():
    from evaluation.harness import _config_path

    assert _config_path("C-noKG").name == "config_c_nokg.yaml"
    assert _config_path("c").name == "config_c.yaml"
