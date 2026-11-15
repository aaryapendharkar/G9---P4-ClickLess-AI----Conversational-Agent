"""Route geometry for the oracle, written independently of clickless.common.geo and retrieval.

Distances in km, coordinates in degrees. The oracle needs only two things from a route: where a
station sits along it (road-scaled km) and how far off it the station is (straight-line km).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

R_KM = 6371.0088  # mean Earth radius (IUGG)

Point = tuple[float, float]  # (lat, lon)


def great_circle_km(a: Point, b: Point) -> float:
    la1, la2 = math.radians(a[0]), math.radians(b[0])
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(math.radians(b[1] - a[1]) / 2) ** 2
    return 2 * R_KM * math.asin(min(1.0, math.sqrt(h)))


def path_length_km(points: Sequence[Point]) -> float:
    return sum(great_circle_km(points[k], points[k + 1]) for k in range(len(points) - 1))


def _xy(p: Point, ref_lat: float) -> tuple[float, float]:
    """Equirectangular projection around ref_lat, in km (accurate for short segments)."""
    return (math.radians(p[1]) * R_KM * math.cos(math.radians(ref_lat)), math.radians(p[0]) * R_KM)


def locate(p: Point, points: Sequence[Point]) -> tuple[float, float]:
    """(along_km, offroute_km) of the closest point on the polyline, both straight-line km.

    along_km is measured along the polyline geometry; callers scale it to road km.
    """
    if len(points) < 2:
        raise ValueError("route geometry needs at least two points")
    best_off, best_along, walked = math.inf, 0.0, 0.0
    for k in range(len(points) - 1):
        a, b = points[k], points[k + 1]
        seg = great_circle_km(a, b)
        ref = (a[0] + b[0]) / 2
        (ax, ay), (bx, by), (px, py) = _xy(a, ref), _xy(b, ref), _xy(p, ref)
        dx, dy = bx - ax, by - ay
        den = dx * dx + dy * dy
        t = 0.0 if den == 0 else min(1.0, max(0.0, ((px - ax) * dx + (py - ay) * dy) / den))
        off = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
        if off < best_off:
            best_off, best_along = off, walked + t * seg
        walked += seg
    return best_along, best_off
