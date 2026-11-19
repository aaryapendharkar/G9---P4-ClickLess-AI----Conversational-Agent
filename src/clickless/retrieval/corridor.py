"""Route-corridor retrieval. Owner: Manas.

Two paths with identical output (issue #94):

  * method="indexed" (default): grid indexes, exact results.
      - Stations and amenities are bucketed into lat/lon grid cells once per snapshot.
      - Per route, each segment is registered in every cell its bounding box, padded by the
        corridor buffer, touches. A station is compared only with the segments registered in its
        cell. The rest provably lie farther than the buffer: point_segment_km measures in a local
        equirectangular projection, where the distance to a segment is at least the projected
        gap to the segment's bounding box (see _segment_pads).
      - Survivors get the same point_segment_km on the same full geometry, in ascending segment
        order with the brute force's tie rule, and the same cumulative distances (computed once
        per route in the same floating-point order). Results are sorted by (along_route_km,
        store order), which is what the brute force's stable sort produces.
      - The scan result depends only on the route (not on the request), so it is memoized per
        route (route_id + a hash of the full geometry, last _ROUTE_MEMO routes): relaxation re-solves
        the same route up to a dozen times. Candidates are rebuilt fresh on every call.
      - The list is cut to max_candidates before amenity linking. Station -> amenity links (same
        haversine expression, same amenity order) are computed once per station per snapshot,
        from the amenity grid, and memoized.
  * method="brute_force": the original reference implementation, every station against every
    segment and every amenity. Kept for the equivalence tests (tests/retrieval/test_corridor.py).

TODO(Manas): a BallTree / R-tree could replace the grids; the equivalence tests stay the contract.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Sequence

from ..common.geo import EARTH_RADIUS_KM, haversine_km, nearest_on_polyline, point_segment_km
from ..contracts import Candidate, NearbyAmenity, Route, TripRequest
from ..data import DataStore

METHODS = ("indexed", "brute_force")
_KM_PER_DEG = EARTH_RADIUS_KM * math.pi / 180  # same radius as common.geo's projection
_STATION_CELL_DEG = 0.1  # station grid and per-route segment grid share this cell size
_AMENITY_CELL_DEG = 0.05
_EPS_KM = 1e-6  # floating-point slack: pads are a hair wider than the exact bound
_ROUTE_MEMO = 8  # routes whose scan result is kept (relaxation reuses the same route)

Cell = tuple[int, int]


def _cell(lat: float, lon: float, size: float) -> Cell:
    return math.floor(lat / size), math.floor(lon / size)


def _cell_range(lo: float, hi: float, size: float) -> range:
    return range(math.floor(lo / size), math.floor(hi / size) + 1)


def _segment_pads(a: tuple[float, float], b: tuple[float, float], buffer_km: float) -> tuple[float, float]:
    """(lat_pad_deg, lon_pad_deg) such that a point outside the segment's bbox padded by them is
    farther than buffer_km from the segment as point_segment_km measures it.

    point_segment_km projects with x = rad(lon) R cos(lat0), y = rad(lat) R, lat0 = mid-latitude of
    the segment. The closest point lies inside the segment's projected bbox, so the distance is
    at least hypot(x gap, y gap) >= each gap alone; a gap > buffer_km means "outside".
    """
    lat_pad = (buffer_km + _EPS_KM) / _KM_PER_DEG
    cos0 = math.cos(math.radians((a[0] + b[0]) / 2))
    lon_pad = lat_pad / cos0 if cos0 > 1e-9 else 360.0
    return lat_pad, lon_pad


class CorridorRetriever:
    def __init__(
        self,
        store: DataStore,
        buffer_km: float,
        amenity_radius_m: float,
        max_candidates: int,
        method: str = "indexed",
    ):
        if method not in METHODS:
            raise ValueError(f"method must be one of {METHODS}, got {method!r}")
        self.store = store
        self.buffer_km = buffer_km
        self.amenity_radius_m = amenity_radius_m
        self.max_candidates = max_candidates
        self.method = method
        self._station_grid: dict[Cell, list[int]] | None = None  # built lazily, once per store
        self._stations: list = []
        self._amenity_grid: dict[Cell, list[int]] | None = None
        self._amenity_links: dict[str, list[tuple[int, float]]] = {}  # station_id -> [(amenity idx, m)]
        self._route_scans: dict[tuple, list[tuple[float, int, int, float]]] = {}  # insertion-ordered memo

    def candidates(self, route: Route, request: TripRequest) -> list[Candidate]:
        if self.method == "brute_force":
            return self._candidates_brute_force(route)
        return self._candidates_indexed(route)

    # ------------------------------------------------------------------ reference implementation
    def _candidates_brute_force(self, route: Route) -> list[Candidate]:
        poly = [(p.lat, p.lon) for p in route.geometry]
        out: list[Candidate] = []
        for st in self.store.stations.values():
            off_km, seg, along = nearest_on_polyline(st.lat, st.lon, poly)
            if off_km > self.buffer_km:
                continue
            nearby = [
                NearbyAmenity(amenity=a, distance_m=d)
                for a in self.store.amenities
                if (d := haversine_km(st.lat, st.lon, a.lat, a.lon) * 1000) <= self.amenity_radius_m
            ]
            out.append(
                Candidate(station=st, route_index=seg, along_route_km=along, offroute_km=off_km, amenities=nearby)
            )
        out.sort(key=lambda c: c.along_route_km)
        return out[: self.max_candidates]

    # ------------------------------------------------------------------ indexed implementation
    def _build_station_grid(self) -> dict[Cell, list[int]]:
        if self._station_grid is None:
            self._stations = list(self.store.stations.values())  # store order = brute-force order
            grid: dict[Cell, list[int]] = defaultdict(list)
            for i, st in enumerate(self._stations):
                grid[_cell(st.lat, st.lon, _STATION_CELL_DEG)].append(i)
            self._station_grid = dict(grid)
        return self._station_grid

    def _segment_grid(self, poly: Sequence[tuple[float, float]]) -> dict[Cell, list[int]]:
        grid: dict[Cell, list[int]] = defaultdict(list)
        for i in range(len(poly) - 1):  # ascending, so every cell's list is in segment order
            a, b = poly[i], poly[i + 1]
            lat_pad, lon_pad = _segment_pads(a, b, self.buffer_km)
            for ci in _cell_range(min(a[0], b[0]) - lat_pad, max(a[0], b[0]) + lat_pad, _STATION_CELL_DEG):
                for cj in _cell_range(min(a[1], b[1]) - lon_pad, max(a[1], b[1]) + lon_pad, _STATION_CELL_DEG):
                    grid[(ci, cj)].append(i)
        return grid

    def _candidates_indexed(self, route: Route) -> list[Candidate]:
        poly = [(p.lat, p.lon) for p in route.geometry]
        if len(poly) < 2:  # degenerate route: nearest_on_polyline has no segment to measure
            return self._candidates_brute_force(route)
        key = (route.route_id, len(poly), hash(tuple(poly)))
        found = self._route_scans.get(key)
        if found is None:
            found = self._scan(poly)
            self._route_scans[key] = found
            while len(self._route_scans) > _ROUTE_MEMO:
                del self._route_scans[next(iter(self._route_scans))]

        out = []
        for along, si, seg, off_km in found[: self.max_candidates]:
            st = self._stations[si]
            nearby = [
                NearbyAmenity(amenity=self.store.amenities[ai], distance_m=d) for ai, d in self._links_for(st)
            ]
            out.append(
                Candidate(station=st, route_index=seg, along_route_km=along, offroute_km=off_km, amenities=nearby)
            )
        return out

    def _scan(self, poly: Sequence[tuple[float, float]]) -> list[tuple[float, int, int, float]]:
        """(along_km, station index, segment, offroute_km) of every corridor station, sorted."""
        stations = self._build_station_grid()
        # cumulative[i] = along-distance at the start of segment i, summed in nearest_on_polyline's order
        seg_len, cumulative, acc = [], [], 0.0
        for i in range(len(poly) - 1):
            length = haversine_km(*poly[i], *poly[i + 1])
            seg_len.append(length)
            cumulative.append(acc)
            acc += length

        found: list[tuple[float, int, int, float]] = []  # (along, station idx, segment, off_km)
        for cell, segments in self._segment_grid(poly).items():
            for si in stations.get(cell, ()):
                st = self._stations[si]
                p = (st.lat, st.lon)
                best_d, best_i, best_t = math.inf, 0, 0.0
                for i in segments:  # ascending; strict < keeps the first minimum, as the brute force does
                    d, t = point_segment_km(p, poly[i], poly[i + 1])
                    if d < best_d:
                        best_d, best_i, best_t = d, i, t
                if best_d <= self.buffer_km:
                    found.append((cumulative[best_i] + best_t * seg_len[best_i], si, best_i, best_d))
        found.sort(key=lambda f: (f[0], f[1]))  # = stable sort by along_route_km of the store-ordered list
        return found

    def _links_for(self, st) -> list[tuple[int, float]]:
        """(amenity index, distance_m) within amenity_radius_m, in store order; memoized per station."""
        if st.station_id in self._amenity_links:
            return self._amenity_links[st.station_id]
        if self._amenity_grid is None:
            grid: dict[Cell, list[int]] = defaultdict(list)
            for i, a in enumerate(self.store.amenities):
                grid[_cell(a.lat, a.lon, _AMENITY_CELL_DEG)].append(i)
            self._amenity_grid = dict(grid)
        # Haversine distance >= R * |dlat| and >= R * cos(max lat) * |dlon| (up to the asin/sin
        # nonlinearity, covered by the factor 2), so a ring of these pads holds every match.
        radius_km = self.amenity_radius_m / 1000 + _EPS_KM
        lat_pad = radius_km / _KM_PER_DEG
        cos_max = math.cos(math.radians(min(89.9, abs(st.lat) + lat_pad)))
        lon_pad = 2 * lat_pad / cos_max
        cand: list[int] = []
        for ci in _cell_range(st.lat - lat_pad, st.lat + lat_pad, _AMENITY_CELL_DEG):
            for cj in _cell_range(st.lon - lon_pad, st.lon + lon_pad, _AMENITY_CELL_DEG):
                cand.extend(self._amenity_grid.get((ci, cj), ()))
        links = []
        for ai in sorted(cand):  # store order, as the brute force iterates
            a = self.store.amenities[ai]
            if (d := haversine_km(st.lat, st.lon, a.lat, a.lon) * 1000) <= self.amenity_radius_m:
                links.append((ai, d))
        self._amenity_links[st.station_id] = links
        return links
