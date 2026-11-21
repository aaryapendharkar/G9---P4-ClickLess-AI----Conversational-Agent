"""The harness records which parser produced each parse, and why an LLM parser fell back to rules."""

import json
import logging

import pytest
import requests
import yaml

from clickless.config import repo_path
from clickless.nlu import llm_parser
from clickless.nlu.llm_parser import LLMParser
from clickless.nlu.rules_parser import RulesParser
from evaluation import harness
from evaluation.parser_trace import NO_LOGGED_REASON, RULES_FALLBACK, ParserTrace

TEXT = "From Phoenix to Flagstaff in my Spark LR, I have 65% battery and want to arrive with at least 20%"
VALID = {
    "origin": "Phoenix",
    "destination": "Flagstaff",
    "vehicle": "Spark LR",
    "start_soc_pct": 65,
    "arrival_soc_pct_min": 20,
    "max_detour_min": None,
    "min_power_kw": None,
    "amenities": [],
    "connectors": [],
}


@pytest.fixture
def llm(store):
    return LLMParser(store, {"model": "test-model", "temperature": 0})


def _timeout(*args, **kwargs):
    raise requests.exceptions.ReadTimeout("Read timed out. (read timeout=60.0)")


def test_llm_success_is_recorded_as_llm(llm, monkeypatch):
    monkeypatch.setattr(llm_parser, "chat", lambda *a, **k: json.dumps(VALID))
    trace = ParserTrace(llm)
    result = trace.parse(TEXT, "t-ok")
    assert trace.last == {"parser_used": "llm", "fallback_reason": None}
    assert result.request.start_soc_pct == 65


def test_timeout_is_recorded_as_rules_fallback_with_the_reason(llm, monkeypatch):
    monkeypatch.setattr(llm_parser, "chat", _timeout)
    trace = ParserTrace(llm)
    traced = trace.parse(TEXT, "t-timeout")
    assert trace.last["parser_used"] == RULES_FALLBACK
    assert "LLM extraction unavailable" in trace.last["fallback_reason"]
    assert "Read timed out" in trace.last["fallback_reason"]
    # observation only: the parse result is exactly what the untraced parser returns
    assert traced == llm.parse(TEXT, "t-timeout")


def test_two_invalid_responses_record_both_validation_errors(llm, monkeypatch):
    monkeypatch.setattr(llm_parser, "chat", lambda *a, **k: "not json")
    trace = ParserTrace(llm)
    trace.parse(TEXT, "t-invalid")
    reason = trace.last["fallback_reason"]
    assert trace.last["parser_used"] == RULES_FALLBACK
    assert "attempt 1 failed validation" in reason and "attempt 2 failed validation" in reason


def test_repaired_response_is_not_a_fallback(llm, monkeypatch):
    responses = iter(['{"unexpected": true}', json.dumps(VALID)])
    monkeypatch.setattr(llm_parser, "chat", lambda *a, **k: next(responses))
    trace = ParserTrace(llm)
    trace.parse(TEXT, "t-repair")
    assert trace.last == {"parser_used": "llm", "fallback_reason": None}


def test_fallback_without_a_warning_still_counts(llm, monkeypatch):
    monkeypatch.setattr(llm_parser, "chat", _timeout)
    monkeypatch.setattr(logging.getLogger("clickless.nlu.llm_parser"), "disabled", True)
    trace = ParserTrace(llm)
    trace.parse(TEXT, "t-silent")
    assert trace.last == {"parser_used": RULES_FALLBACK, "fallback_reason": NO_LOGGED_REASON}


def test_non_llm_parser_is_recorded_by_name(store):
    trace = ParserTrace(RulesParser(store))
    trace.parse(TEXT, "t-rules")
    assert trace.last == {"parser_used": "rules", "fallback_reason": None}


def test_parser_is_left_unchanged_even_when_parse_raises(llm, monkeypatch):
    class BrokenRules:  # LLMParser catches errors around the LLM call, but not inside its fallback
        def __init__(self, store):
            raise RuntimeError("bug inside the fallback")

    monkeypatch.setattr(llm_parser, "RulesParser", BrokenRules)
    monkeypatch.setattr(llm_parser, "chat", _timeout)
    with pytest.raises(RuntimeError, match="inside the fallback"):
        ParserTrace(llm).parse(TEXT, "t-raise")
    assert "_fallback" not in vars(llm)
    assert not [h for h in logging.getLogger("clickless.nlu.llm_parser").handlers if type(h).__name__ == "_Collect"]


def test_a_fallback_reported_by_the_parse_result_is_used_first():
    class Result:
        fallback_reason = "LLM extraction unavailable: endpoint down"

    class ReportingParser:
        name = "llm"

        def _fallback(self, text, request_id):  # never called: the result reports the fallback itself
            raise AssertionError

        def parse(self, text, request_id):
            return Result()

    trace = ParserTrace(ReportingParser())
    trace.parse(TEXT, "t-reported")
    assert trace.last == {"parser_used": RULES_FALLBACK, "fallback_reason": "LLM extraction unavailable: endpoint down"}


def test_wrapper_forwards_other_attributes(llm):
    assert ParserTrace(llm).name == "llm" and ParserTrace(llm).store is llm.store


# --------------------------------------------------------------------------- harness rows


def _llm_config(tmp_path):
    """Config A on the fixture world, with the LLM parser (no SBERT download, no endpoint)."""
    cfg = yaml.safe_load(repo_path("configs/config_a.yaml").read_text(encoding="utf-8"))
    cfg["name"] = "B_rules_fallback_test"
    cfg["components"]["parser"] = "llm"
    cfg["llm"] = {"model": "test-model", "temperature": 0}
    path = tmp_path / "config_llm.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path


def _run(tmp_path, monkeypatch, config):
    monkeypatch.setattr(harness, "run_metadata", lambda cfg, text="": {"config": cfg["name"]})
    out = harness.run(config, items_paths=["benchmark/requests/dev/fixture_smoke.jsonl"],
                      out_dir=str(tmp_path), n_boot=20)
    return json.loads(out.read_text(encoding="utf-8"))["rows"]


def test_harness_rows_record_a_rules_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_parser, "chat", _timeout)
    rows = _run(tmp_path, monkeypatch, str(_llm_config(tmp_path)))
    assert rows and all(r["parser_used"] == RULES_FALLBACK for r in rows)
    assert all("Read timed out" in r["fallback_reason"] for r in rows)


def test_harness_rows_record_the_llm_when_it_answers(tmp_path, monkeypatch):
    monkeypatch.setattr(llm_parser, "chat", lambda *a, **k: json.dumps(VALID))
    rows = _run(tmp_path, monkeypatch, str(_llm_config(tmp_path)))
    assert rows and all(r["parser_used"] == "llm" and r["fallback_reason"] is None for r in rows)


def test_harness_rows_record_rules_for_config_a(tmp_path, monkeypatch):
    rows = _run(tmp_path, monkeypatch, "A")
    assert rows and all(r["parser_used"] == "rules" and r["fallback_reason"] is None for r in rows)
