"""Google Maps directions URLs with ordered waypoints. Owner: Aarya.

Format (Maps URLs, api=1): https://www.google.com/maps/dir/?api=1&origin=LAT,LON
    &destination=LAT,LON&waypoints=LAT,LON|LAT,LON&travelmode=driving
TODO(Aarya):
  * confirm the waypoint limit per platform in the docs and put it in configs (currently 9)
  * segmentation when stops exceed the limit: every approved stop exactly once, and segment k's
    destination == segment k+1's origin (spec in tests/handoff/test_maps_url.py)
  * decide: coordinates vs place names in the URL (coordinates are unambiguous)
"""

from __future__ import annotations

from urllib.parse import urlencode

from ..contracts import Itinerary, LatLon, Route
from ..data import DataStore

BASE_URL = "https://www.google.com/maps/dir/"


def _fmt(p: LatLon) -> str:
    return f"{p.lat:.6f},{p.lon:.6f}"


def build_url(origin: LatLon, destination: LatLon, waypoints: list[LatLon]) -> str:
    params = {"api": "1", "origin": _fmt(origin), "destination": _fmt(destination), "travelmode": "driving"}
    if waypoints:
        params["waypoints"] = "|".join(_fmt(w) for w in waypoints)
    return BASE_URL + "?" + urlencode(params, safe=",|")


class GoogleMapsHandoff:
    def __init__(self, store: DataStore, max_waypoints: int) -> None:
        self.store = store
        self.max_waypoints = max_waypoints

    def build(self, itinerary: Itinerary, route: Route) -> list[str]:
        stops = [self.store.station(s.station_id) for s in itinerary.stops]
        waypoints = [LatLon(lat=s.lat, lon=s.lon) for s in stops]
        if len(waypoints) <= self.max_waypoints:
            return [build_url(route.origin, route.destination, waypoints)]
        raise NotImplementedError("TODO(Aarya): split into segments preserving boundaries")
