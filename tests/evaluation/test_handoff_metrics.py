import json

from clickless.contracts import LatLon
from clickless.handoff.maps_url import build_url
from evaluation.metrics.handoff import CHECKS, check_urls, handoff_correctness, handoff_metrics, main, read_audit

ORIGIN = LatLon(lat=33.000000, lon=-112.000000)
STOP_ONE = LatLon(lat=34.000000, lon=-111.000000)
STOP_TWO = LatLon(lat=35.000000, lon=-110.000000)
DESTINATION = LatLon(lat=36.000000, lon=-109.000000)


def test_valid_single_handoff_has_ordered_unique_stops():
    url = build_url(ORIGIN, DESTINATION, [STOP_ONE, STOP_TWO])

    assert check_urls(urls=[url], expected_stops=[(STOP_ONE.lat, STOP_ONE.lon), (STOP_TWO.lat, STOP_TWO.lon)]) == {
        "parses": True,
        "ordered": True,
        "each_stop_once": True,
        "boundaries_ok": True,
    }


def test_invalid_url_fails_all_validation_metrics():
    assert check_urls(["https://example.com/not-a-maps-url"], [(STOP_ONE.lat, STOP_ONE.lon)]) == {
        "parses": False,
        "ordered": False,
        "each_stop_once": False,
        "boundaries_ok": False,
    }


def test_wrong_stop_order_is_reported():
    url = build_url(ORIGIN, DESTINATION, [STOP_TWO, STOP_ONE])

    result = check_urls([url], [(STOP_ONE.lat, STOP_ONE.lon), (STOP_TWO.lat, STOP_TWO.lon)])

    assert result["parses"] and not result["ordered"] and result["each_stop_once"]


def test_duplicate_approved_stop_is_not_counted_once():
    url = build_url(ORIGIN, DESTINATION, [STOP_ONE, STOP_ONE])

    result = check_urls([url], [(STOP_ONE.lat, STOP_ONE.lon)])

    assert result["parses"] and not result["each_stop_once"]


def test_segment_boundary_is_allowed_once_but_not_counted_twice():
    first_segment = build_url(ORIGIN, STOP_ONE, [])
    second_segment = build_url(STOP_ONE, DESTINATION, [STOP_TWO])

    result = check_urls(
        [first_segment, second_segment],
        [(STOP_ONE.lat, STOP_ONE.lon), (STOP_TWO.lat, STOP_TWO.lon)],
    )

    assert result == {
        "parses": True,
        "ordered": True,
        "each_stop_once": True,
        "boundaries_ok": True,
    }


def test_disconnected_segments_fail_boundary_check():
    first_segment = build_url(ORIGIN, STOP_ONE, [])
    disconnected_segment = build_url(STOP_TWO, DESTINATION, [])

    result = check_urls([first_segment, disconnected_segment], [(STOP_ONE.lat, STOP_ONE.lon)])

    assert result["parses"] and result["ordered"] and result["each_stop_once"]
    assert not result["boundaries_ok"]


def _row(item_id, urls, stops=(STOP_ONE,), **extra):
    expected = [{"station_id": f"s{i}", "lat": s.lat, "lon": s.lon} for i, s in enumerate(stops)]
    return {"id": item_id, "status": "ok", "handoff_urls": urls, "expected_stops": expected, **extra}


def test_summary_counts_correct_direct_and_segmented_handoffs():
    segmented = [build_url(ORIGIN, STOP_ONE, []), build_url(STOP_ONE, DESTINATION, [])]
    rows = [_row("direct", [build_url(ORIGIN, DESTINATION, [])], stops=()), _row("segmented", segmented)]

    report = handoff_metrics(rows)

    assert report["correct"] == {"passed": 2, "n": 2, "rate": 1.0}
    assert set(report["checks"]) == set(CHECKS) and report["failures"] == []


def test_summary_fails_a_waypoint_the_driver_did_not_approve():
    rows = [_row("extra", [build_url(ORIGIN, DESTINATION, [STOP_ONE, STOP_TWO])])]

    report = handoff_metrics(rows)

    assert report["correct"]["passed"] == 0
    assert report["failures"] == [{"id": "extra", "failed": ["no_extra_stops"]}]


def test_summary_keeps_errors_and_unplanned_rows_visible():
    rows = [
        _row("good", [build_url(ORIGIN, DESTINATION, [STOP_ONE])]),
        _row("raised", [], stops=(), handoff_error="ValueError: boom"),
        {"id": "old", "status": "ok"},
        {"id": "crash", "status": "error"},
        {"id": "rejected", "status": "incomplete"},
    ]

    report = handoff_metrics(rows)

    assert report["n_rows"] == 5
    assert report["rows_by_status"] == {"ok": 3, "error": 1, "incomplete": 1}
    assert report["correct"] == {"passed": 1, "n": 2, "rate": 0.5}
    assert report["n_handoff_errors"] == 1 and report["unscored_ok_ids"] == ["old"]
    assert report["failures"] == [{"id": "raised", "failed": list(CHECKS), "error": "ValueError: boom"}]


def test_summary_has_no_rate_without_scored_rows():
    report = handoff_metrics([{"id": "crash", "status": "error"}])

    assert report["correct"] == {"passed": 0, "n": 0, "rate": None}


def test_command_line_summarizes_a_saved_run(tmp_path, capsys):
    run = tmp_path / "A_dev.json"
    rows = [_row("good", [build_url(ORIGIN, DESTINATION, [STOP_ONE])])]
    run.write_text(json.dumps({"config": "A", "rows": rows}), encoding="utf-8")

    main([str(run)])

    report = json.loads(capsys.readouterr().out)
    assert report["config"] == "A" and report["correct"]["rate"] == 1.0


# --------------------------------------------------------------------------- handoff_correctness (#185)


def _planned(item_id, urls, plan=("s0",), approved=False):
    """An ok row whose saved result recommends `plan` from ORIGIN to DESTINATION."""
    stops = [{"station_id": sid} for sid in plan]
    result = {
        "status": "ok",
        "approved": approved,
        "handoff_urls": [],
        "request": {"origin": {"lat": ORIGIN.lat, "lon": ORIGIN.lon},
                    "destination": {"lat": DESTINATION.lat, "lon": DESTINATION.lon}},
        "alternatives": [{"itinerary": {"stops": stops}}],
    }
    return {**_row(item_id, urls), "result": result}


def _audit(*ids, urls=1):
    return [{"request_id": i, "approved": True, "urls": urls} for i in ids]


GOOD_URL = build_url(ORIGIN, DESTINATION, [STOP_ONE])


def test_correct_handoff_passes_every_check():
    report = handoff_correctness([_planned("good", [GOOD_URL])], _audit("good"))

    assert report["fully_correct"] == {"passed": 1, "n": 1, "rate": 1.0}
    assert all(v["rate"] == 1.0 for v in report["plan_checks"].values())
    assert report["plan_failures"] == [] and report["approval"]["saved_results_approved_or_with_urls"] == []


def test_stops_must_be_the_saved_recommendation():
    report = handoff_correctness([_planned("other", [GOOD_URL], plan=("another-station",))], _audit("other"))

    assert report["plan_failures"] == [{"id": "other", "failed": ["matches_plan"]}]
    assert report["fully_correct"]["passed"] == 0


def test_handoff_must_start_at_the_origin_and_end_at_the_destination():
    wrong_end = build_url(ORIGIN, STOP_TWO, [STOP_ONE])

    report = handoff_correctness([_planned("end", [wrong_end])], _audit("end"))

    assert report["plan_failures"] == [{"id": "end", "failed": ["endpoints_ok"]}]


def test_approval_needs_exactly_one_matching_audit_entry():
    rows = [_planned("none", [GOOD_URL]), _planned("twice", [GOOD_URL]), _planned("count", [GOOD_URL])]
    audit = [*_audit("twice", "twice"), *_audit("count", urls=2)]

    report = handoff_correctness(rows, audit)

    assert {f["id"]: f["failed"] for f in report["plan_failures"]} == {
        "none": ["approved_in_audit"], "twice": ["approved_in_audit"], "count": ["approved_in_audit"]
    }
    assert report["fully_correct"]["passed"] == 0


def test_without_an_audit_log_approval_is_not_scored():
    report = handoff_correctness([_planned("good", [GOOD_URL])], None)

    assert report["plan_checks"]["approved_in_audit"] is None and not report["approval"]["audit_found"]
    assert report["fully_correct"]["passed"] == 1  # the other checks still hold


def test_saved_result_must_stay_unapproved():
    report = handoff_correctness([_planned("leak", [GOOD_URL], approved=True)], _audit("leak"))

    assert report["approval"]["saved_results_approved_or_with_urls"] == ["leak"]


def test_rejected_items_get_no_handoff():
    rows = [
        _planned("good", [GOOD_URL]),
        {"id": "rejected", "status": "rejected_incomplete"},
        {"id": "approved-anyway", "status": "infeasible"},
        {"id": "urls-anyway", "status": "needs_clarification", "handoff_urls": [GOOD_URL]},
    ]

    report = handoff_correctness(rows, _audit("good", "approved-anyway"))

    assert report["non_ok"] == {"n": 3, "with_handoff": ["approved-anyway", "urls-anyway"]}
    assert report["approval"]["audit_ids_not_ok"] == ["approved-anyway"]


def test_url_failures_and_handoff_errors_are_never_fully_correct():
    rows = [_planned("extra", [build_url(ORIGIN, DESTINATION, [STOP_ONE, STOP_TWO])]),
            {**_planned("raised", []), "handoff_error": "ValueError: boom", "expected_stops": []}]

    report = handoff_correctness(rows, _audit("extra", "raised", urls=1))

    assert report["fully_correct"] == {"passed": 0, "n": 2, "rate": 0.0}


def test_read_audit(tmp_path):
    path = tmp_path / "A_handoff_audit.jsonl"
    assert read_audit(path) is None
    path.write_text(json.dumps(_audit("good")[0]) + "\n\n", encoding="utf-8")
    assert read_audit(path) == _audit("good")
