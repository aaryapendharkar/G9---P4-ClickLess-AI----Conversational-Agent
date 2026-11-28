import json

from clickless.contracts import LatLon
from clickless.handoff.maps_url import build_url
from evaluation.metrics.handoff import CHECKS, check_urls, handoff_metrics, main

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
