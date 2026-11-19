"""Speed-only corridor index for the oracle: same numbers as the exhaustive path, far less work.

Written for the oracle, independently of clickless.retrieval (test_oracle_independence.py).

The exhaustive path calls _geometry.locate for every station in the store against every segment
of the route. Here:

  * RouteGeometry precomputes, once per route, each segment's great-circle length and the
    distance walked before it (accumulated in the same order as locate), so locating a station
    on a subset of segments gives bit-identical (along_km, offroute_km).
  * StationGrid buckets the stations into lat/lon cells once per store.
  * corridor() registers each route segment in every cell its bounding box, padded by the
    buffer, touches, and locates only the stations in those cells, against only that cell's
    segments. Every other segment is provably farther than the buffer: locate measures in an
    equirectangular projection where y = rad(lat) R and x = rad(lon) R cos(mid-lat of the
    segment), and the closest point of a segment lies inside its projected bounding box, so the
    distance is at least the gap in y and at least the gap in x. A station whose true minimum is
    within the buffer therefore gets the same minimum, on the same (first) segment, as locate.
    Stations in no registered cell are beyond the buffer.

RouteIndex keeps this per route (geometry, exact per-station projections, the corridor per
buffer) so is_feasible, pareto_plans and check_itinerary on the same request reuse it.
"""

from __future__ import annotations

import math
from collections import OrderedDict, defaultdict
from collections.abc import Sequence

from . import _geometry as geo

CELL_DEG = 0.1
SLACK_KM = 1e-3  # pads are 1 m wider than the buffer: far above floating-point error
_KM_PER_DEG = geo.R_KM * math.pi / 180  # same radius as _geometry's projection
MAX_ROUTES = 8  # routes kept in RouteIndex (one evaluation run touches a few at a time)

Cell = tuple[int, int]
Located = tuple[float, float]  # (along straight-line km on the geometry, offroute km), as geo.locate


def _cell(lat: float, lon: float) -> Cell:
    return math.floor(lat / CELL_DEG), math.floor(lon / CELL_DEG)


def _cells(lo: float, hi: float) -> range:
    return range(math.floor(lo / CELL_DEG), math.floor(hi / CELL_DEG) + 1)


class RouteGeometry:
    def __init__(self, points: Sequence[geo.Point]) -> None:
        if len(points) < 2:
            raise ValueError("route geometry needs at least two points")
        self.points = list(points)
        self.seg_km: list[float] = []
        self.walked_km: list[float] = []  # distance before segment k, summed exactly as geo.locate does
        walked = 0.0
        for k in range(len(points) - 1):
            seg = geo.great_circle_km(points[k], points[k + 1])
            self.seg_km.append(seg)
            self.walked_km.append(walked)
            walked += seg

    def locate(self, p: geo.Point, segments: Sequence[int] | None = None) -> Located:
        """geo.locate restricted to `segments` (ascending); all segments when None."""
        best_off, best_along = math.inf, 0.0
        pts = self.points
        for k in range(len(pts) - 1) if segments is None else segments:
            t, off = geo.project(p, pts[k], pts[k + 1])
            if off < best_off:
                best_off, best_along = off, self.walked_km[k] + t * self.seg_km[k]
        return best_along, best_off

    def segment_cells(self, buffer_km: float) -> dict[Cell, list[int]]:
        """cell -> segments (ascending) whose padded bounding box touches the cell."""
        grid: dict[Cell, list[int]] = defaultdict(list)
        lat_pad = (buffer_km + SLACK_KM) / _KM_PER_DEG
        pts = self.points
        for k in range(len(pts) - 1):
            (a_lat, a_lon), (b_lat, b_lon) = pts[k], pts[k + 1]
            cos_ref = math.cos(math.radians((a_lat + b_lat) / 2))
            lon_pad = lat_pad / cos_ref if cos_ref > 1e-9 else 360.0
            for ci in _cells(min(a_lat, b_lat) - lat_pad, max(a_lat, b_lat) + lat_pad):
                for cj in _cells(min(a_lon, b_lon) - lon_pad, max(a_lon, b_lon) + lon_pad):
                    grid[(ci, cj)].append(k)
        return grid


class StationGrid:
    """Station ids bucketed by cell, in store order."""

    def __init__(self, stations) -> None:
        cells: dict[Cell, list[str]] = defaultdict(list)
        self.coords: dict[str, geo.Point] = {}
        for st in stations.values():
            cells[_cell(st.lat, st.lon)].append(st.station_id)
            self.coords[st.station_id] = (st.lat, st.lon)
        self.cells = dict(cells)


class _RouteEntry:
    def __init__(self, points: Sequence[geo.Point]) -> None:
        self.geometry = RouteGeometry(points)
        self.located: dict[str, Located] = {}  # exact, any station
        self.corridors: dict[float, dict[str, Located]] = {}  # buffer -> stations within it


class RouteIndex:
    """Shared by an oracle and its with_overrides copies; reset when the store's stations change."""

    def __init__(self) -> None:
        self._grid: StationGrid | None = None
        self._signature: tuple | None = None
        self._routes: OrderedDict[tuple, _RouteEntry] = OrderedDict()

    def _sync(self, stations) -> StationGrid:
        signature = (id(stations), len(stations))
        if signature != self._signature:
            self._grid, self._signature = StationGrid(stations), signature
            self._routes.clear()
        return self._grid

    def _entry(self, key: tuple, points: Sequence[geo.Point]) -> _RouteEntry:
        entry = self._routes.get(key)
        if entry is None:
            entry = self._routes[key] = _RouteEntry(points)
            while len(self._routes) > MAX_ROUTES:
                self._routes.popitem(last=False)
        else:
            self._routes.move_to_end(key)
        return entry

    def corridor(self, stations, key: tuple, points: Sequence[geo.Point], buffer_km: float) -> dict[str, Located]:
        """station_id -> geo.locate result, for every station whose offroute km <= buffer_km + 1e-9
        (the oracle's own inclusion test), and only those."""
        grid = self._sync(stations)
        entry = self._entry(key, points)
        found = entry.corridors.get(buffer_km)
        if found is not None:
            return found
        found = {}
        limit = buffer_km + 1e-9
        if not math.isfinite(buffer_km):  # no buffer: every station is in the corridor
            for sid, p in grid.coords.items():
                found[sid] = self._locate(entry, sid, p)
        else:
            for cell, segments in entry.geometry.segment_cells(buffer_km).items():
                for sid in grid.cells.get(cell, ()):
                    hit = entry.located.get(sid) or entry.geometry.locate(grid.coords[sid], segments)
                    if hit[1] <= limit:
                        entry.located[sid] = hit
                        found[sid] = hit
        entry.corridors[buffer_km] = found
        return found

    def locate(self, stations, key: tuple, points: Sequence[geo.Point], station) -> Located:
        """Exact geo.locate for any station, memoized per route."""
        self._sync(stations)
        return self._locate(self._entry(key, points), station.station_id, (station.lat, station.lon))

    @staticmethod
    def _locate(entry: _RouteEntry, sid: str, p: geo.Point) -> Located:
        hit = entry.located.get(sid)
        if hit is None:
            hit = entry.located[sid] = entry.geometry.locate(p)
        return hit
