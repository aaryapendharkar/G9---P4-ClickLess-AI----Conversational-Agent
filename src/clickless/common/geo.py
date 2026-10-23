"""Geometry helpers on (lat, lon) in degrees. Distances in km."""

from __future__ import annotations

import math
from collections.abc import Sequence

EARTH_RADIUS_KM = 6371.0088


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def _project(lat: float, lon: float, lat0: float) -> tuple[float, float]:
    """Local equirectangular projection to km. Fine for distances of a few hundred km."""
    x = math.radians(lon) * EARTH_RADIUS_KM * math.cos(math.radians(lat0))
    y = math.radians(lat) * EARTH_RADIUS_KM
    return x, y


def point_segment_km(
    p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> tuple[float, float]:
    """Distance from point p to segment ab, and t in [0, 1] = position of the closest point on ab."""
    lat0 = (a[0] + b[0]) / 2
    px, py = _project(*p, lat0)
    ax, ay = _project(*a, lat0)
    bx, by = _project(*b, lat0)
    dx, dy = bx - ax, by - ay
    seg2 = dx * dx + dy * dy
    t = 0.0 if seg2 == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg2))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy), t


def polyline_length_km(points: Sequence[tuple[float, float]]) -> float:
    return sum(haversine_km(*points[i], *points[i + 1]) for i in range(len(points) - 1))


def nearest_on_polyline(
    lat: float, lon: float, points: Sequence[tuple[float, float]]
) -> tuple[float, int, float]:
    """Return (offroute_km, segment_index, along_km) for the closest point on the polyline.

    along_km is straight-line distance along the geometry; scale by route.distance_km /
    polyline_length_km(geometry) to approximate road distance.
    """
    best = (math.inf, 0, 0.0)
    cumulative = 0.0
    for i in range(len(points) - 1):
        seg_len = haversine_km(*points[i], *points[i + 1])
        d, t = point_segment_km((lat, lon), points[i], points[i + 1])
        if d < best[0]:
            best = (d, i, cumulative + t * seg_len)
        cumulative += seg_len
    return best
