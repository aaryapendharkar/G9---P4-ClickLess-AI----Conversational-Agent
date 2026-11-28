"""Latency p50 / p95 from saved harness rows (#185), hand-computed."""

import json

import pytest

from evaluation.metrics.latency import latency_summary, main


def ok_row(item_id, ms, timings, status="ok"):
    return {"id": item_id, "status": status, "latency_ms": ms, "result": {"timings_ms": timings}}


ROWS = [
    ok_row("a", 100.0, {"parse": 10.0, "route": 20.0, "explain": 1.0}),
    ok_row("b", 200.0, {"parse": 20.0, "route": 40.0, "explain": 1.0}),
    ok_row("c", 300.0, {"parse": 30.0, "route": 60.0, "explain": 1.0}),
    ok_row("r", 5.0, {"parse": 4.0, "explain": 1.0}, status="rejected_incomplete"),
    # An error row keeps its latency and the laps recorded before the exception, in its own timings_ms.
    {"id": "e", "status": "error", "latency_ms": 400.0, "timings_ms": {"parse": 40.0, "route": 80.0}},
]


def test_request_percentiles_include_error_rows():
    s = latency_summary(ROWS)

    # 5, 100, 200, 300, 400 -> p50 = 200; p95 = 300 + 0.8 * 100 (linear interpolation)
    assert s["request"] == {"n": 5, "p50": 200.0, "p95": pytest.approx(380.0), "mean": 201.0, "max": 400.0}
    assert (s["n_rows"], s["n_error_rows"], s["n_error_rows_timed"]) == (5, 1, 1)
    assert s["request_ok_only"]["n"] == 4 and s["request_ok_only"]["max"] == 300.0


def test_routed_scope_leaves_out_early_rejections():
    s = latency_summary(ROWS)

    assert s["request_routed"]["n"] == 4  # a, b, c and the error row reached routing; r did not
    assert s["request_routed"]["p50"] == 250.0


def test_stage_percentiles_in_pipeline_order_with_their_own_counts():
    s = latency_summary(ROWS)

    assert list(s["stages"]) == ["parse", "route", "explain"]
    assert s["stages"]["parse"]["n"] == 5 and s["stages"]["parse"]["p50"] == 20.0
    assert s["stages"]["route"]["n"] == 4 and s["stages"]["route"]["max"] == 80.0  # error row's lap counts
    assert s["stages"]["explain"]["n"] == 4


def test_rows_without_latency_are_listed_not_dropped():
    s = latency_summary([*ROWS, {"id": "old", "status": "not_implemented"}])

    assert s["missing_latency_ids"] == ["old"]
    assert s["request"]["n"] == 5 and s["n_error_rows"] == 2 and s["n_error_rows_timed"] == 1


def test_empty_run_has_no_percentiles():
    assert latency_summary([])["request"] == {"n": 0, "p50": None, "p95": None, "mean": None, "max": None}


def test_command_line_summarizes_a_saved_run(tmp_path, capsys):
    run = tmp_path / "A_dev.json"
    run.write_text(json.dumps({"config": "A", "rows": ROWS}), encoding="utf-8")

    main([str(run)])

    out = json.loads(capsys.readouterr().out)
    assert out["config"] == "A" and out["request"]["p50"] == 200.0
