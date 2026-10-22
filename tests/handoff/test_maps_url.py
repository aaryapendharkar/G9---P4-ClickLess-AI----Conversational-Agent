from urllib.parse import parse_qs, urlparse

import pytest

from clickless.contracts import LatLon
from clickless.handoff.maps_url import build_url


def test_url_has_ordered_waypoints():
    url = build_url(LatLon(lat=1, lon=2), LatLon(lat=5, lon=6), [LatLon(lat=3, lon=4), LatLon(lat=4, lon=5)])
    q = parse_qs(urlparse(url).query)
    assert q["api"] == ["1"] and q["travelmode"] == ["driving"]
    assert q["waypoints"] == ["3.000000,4.000000|4.000000,5.000000"]


def test_no_waypoints_param_for_direct_trip():
    assert "waypoints" not in build_url(LatLon(lat=1, lon=2), LatLon(lat=5, lon=6), [])


@pytest.mark.xfail(raises=NotImplementedError, reason="TODO(Aarya): segmentation beyond waypoint limit")
def test_segmentation_keeps_every_stop_once(pipe_a, demo_text):
    r = pipe_a.run(demo_text, "h1")
    pipe_a.handoff.max_waypoints = 0  # force segmentation
    urls = pipe_a.approve(r).handoff_urls
    assert len(urls) == 2
