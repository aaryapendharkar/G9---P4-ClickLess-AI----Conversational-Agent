"""Google Maps directions URLs with ordered waypoints. Owner: Aarya.

Format (Maps URLs, api=1): https://www.google.com/maps/dir/?api=1&origin=LAT,LON
    &destination=LAT,LON&waypoints=LAT,LON|LAT,LON&travelmode=driving
Decisions (ADR 0010 §handoff):
  * waypoint limit: up to 3 waypoints open on mobile, up to 9 elsewhere; drivers use phones, so
    configs use 3 (`handoff.max_waypoints`);
  * more stops than that: split into consecutive URLs. Every approved stop appears exactly once,
    and segment k's destination is segment k+1's origin (the driver continues from that stop);
  * coordinates, not place names, everywhere (unambiguous, and they match the verified stops).
"""

from __future__ import annotations

from urllib.parse import urlencode

from ..contracts import Itinerary, LatLon, Route
from ..data import DataStore

BASE_URL = "https://www.google.com/maps/dir/"


def _fmt(p: LatLon) -> str:
    return f"{p.lat:.6f},{p.lon:.6f}"


def segment_points(points: list[LatLon], max_waypoints: int) -> list[list[LatLon]]:
    """Split [origin, stop1, ..., stopN, destination] into URL segments of at most
    `max_waypoints` intermediate points; consecutive segments share their boundary point."""
    if max_waypoints < 0:
        raise ValueError("max_waypoints must be >= 0")
    if len(points) < 2:
        raise ValueError("need at least an origin and a destination")
    step = max_waypoints + 1
    return [points[i : i + step + 1] for i in range(0, len(points) - 1, step)]


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
        points = [route.origin, *waypoints, route.destination]
        return [build_url(seg[0], seg[-1], seg[1:-1]) for seg in segment_points(points, self.max_waypoints)]
