"""#139: the driver sees one "Not used:" summary line, not a dump of afdc: station IDs. The
per-station "skipped <id>: <reason>" notes stay in PlanResult.notes for the eval."""

import re

import numpy as np
import pytest

import clickless.cli as cli
from clickless.config import load_config
from clickless.contracts import PlanResult
from clickless.explain.templates import _skipped_summary
from clickless.pipeline import build_pipeline
from clickless.retrieval.embedding_matcher import EmbeddingMatcher


def test_the_summary_counts_stations_by_reason():
    notes = [
        "skipped afdc:1: connector",
        "skipped afdc:2: connector",
        "skipped afdc:3: status:temporarily_unavailable",
        "skipped afdc:4: access:restricted",
        "some other diagnostic",
    ]
    assert _skipped_summary(notes) == (
        "Not used: 4 stations on the route (2 incompatible connector, 1 restricted access, 1 temporarily unavailable)."
    )
    assert (
        _skipped_summary(["skipped afdc:9: power"]) == "Not used: 1 station on the route (1 below your minimum power)."
    )
    assert _skipped_summary(["nothing skipped"]) is None


@pytest.mark.parametrize("config", ["A", "B", "C"])
def test_the_explanation_has_one_summary_line_and_no_station_ids(store, demo_text, config):
    pipe = build_pipeline(load_config(config), store)
    if isinstance(pipe.matcher, EmbeddingMatcher):  # B/C: no Sentence-BERT download in CI
        pipe.matcher = EmbeddingMatcher(store, encoder=lambda texts: np.ones((len(texts), 8)))
    req = _rule_parsed_request(store, demo_text)
    result = pipe.plan_request(req, PlanResult(request_id=req.request_id, config=config, status="ok", request=req))
    skipped = [n for n in result.notes if n.startswith("skipped ")]
    assert result.status == "ok" and skipped  # the fixture demo rules out some stations

    not_used = [line for line in result.explanation.splitlines() if line.startswith("Not used:")]
    assert len(not_used) == 1
    assert re.fullmatch(rf"Not used: {len(skipped)} stations? on the route \(.+\)\.", not_used[0])
    for note in skipped:  # no station ID reaches the driver
        station_id = note.removeprefix("skipped ").rsplit(": ", 1)[0]
        assert station_id not in result.explanation


def _rule_parsed_request(store, demo_text):
    # Config A's rule parser gives a deterministic request for every config (B/C parse with the LLM)
    return build_pipeline(load_config("A"), store).parser.parse(demo_text, "not-used").request


class _OnePipeline:
    def __init__(self, result):
        self.result = result

    def run(self, text):
        return self.result


def test_the_cli_does_not_print_the_per_station_notes(pipe_a, demo_text, monkeypatch, capsys):
    result = pipe_a.run(demo_text, request_id="cli-not-used")
    skipped = [n for n in result.notes if n.startswith("skipped ")]
    assert skipped
    monkeypatch.setattr(cli, "build_pipeline", lambda _cfg: _OnePipeline(result))

    assert cli.main(["plan", "--no-input", demo_text]) == 0
    out = capsys.readouterr().out
    assert "Not used:" in out and "[notes] skipped" not in out
    assert not any(note in out for note in skipped)

    assert cli.main(["plan", "--no-input", "--json", demo_text]) == 0
    out = capsys.readouterr().out
    assert all(note in out for note in skipped)  # the full list is still in the saved result
