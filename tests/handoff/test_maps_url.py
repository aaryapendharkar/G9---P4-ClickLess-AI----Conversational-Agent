from urllib.parse import parse_qs, urlparse

import pytest

from clickless.contracts import LatLon
from clickless.handoff.maps_url import build_url, segment_points
from evaluation.metrics.handoff import check_urls


def test_url_has_ordered_waypoints():
    url = build_url(LatLon(lat=1, lon=2), LatLon(lat=5, lon=6), [LatLon(lat=3, lon=4), LatLon(lat=4, lon=5)])
    q = parse_qs(urlparse(url).query)
    assert q["api"] == ["1"] and q["travelmode"] == ["driving"]
    assert q["waypoints"] == ["3.000000,4.000000|4.000000,5.000000"]


def test_no_waypoints_param_for_direct_trip():
    assert "waypoints" not in build_url(LatLon(lat=1, lon=2), LatLon(lat=5, lon=6), [])


def test_segmentation_keeps_every_stop_once(pipe_a, demo_text):
    r = pipe_a.run(demo_text, "h1")
    n_stops = len(r.alternatives[0].itinerary.stops)
    pipe_a.handoff.max_waypoints = 0  # force segmentation
    try:
        urls = pipe_a.approve(r).handoff_urls
    finally:
        pipe_a.handoff.max_waypoints = 3
    assert len(urls) == n_stops + 1
    stops = [pipe_a.store.station(s.station_id) for s in r.alternatives[0].itinerary.stops]
    checks = check_urls(urls, [(s.lat, s.lon) for s in stops])
    assert all(checks.values()), checks


def _pts(n):
    return [LatLon(lat=float(i), lon=float(i)) for i in range(n)]


@pytest.mark.parametrize("n_stops, limit, n_urls", [(0, 3, 1), (3, 3, 1), (4, 3, 2), (7, 3, 2), (8, 3, 3), (2, 0, 3)])
def test_segment_points_counts_and_boundaries(n_stops, limit, n_urls):
    points = _pts(n_stops + 2)
    segs = segment_points(points, limit)
    assert len(segs) == n_urls
    assert all(len(s) - 2 <= limit for s in segs)
    assert all(a[-1] == b[0] for a, b in zip(segs[:-1], segs[1:], strict=True))  # k's destination == k+1's origin
    flat = [p for i, s in enumerate(segs) for p in (s if i == 0 else s[1:])]
    assert flat == points  # every point exactly once, in order


def test_segment_points_rejects_bad_input():
    with pytest.raises(ValueError):
        segment_points(_pts(3), -1)
    with pytest.raises(ValueError):
        segment_points(_pts(1), 3)


def test_configs_use_the_mobile_waypoint_limit():
    from clickless.config import load_config

    assert {load_config(c)["handoff"]["max_waypoints"] for c in "ABC"} == {3}
