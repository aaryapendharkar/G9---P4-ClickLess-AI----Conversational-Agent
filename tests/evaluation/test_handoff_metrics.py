from clickless.contracts import LatLon
from clickless.handoff.maps_url import build_url
from evaluation.metrics.handoff import check_urls

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
