"""Route-corridor retrieval. Owner: Manas.

Same result as the original brute force (every station's distance to the route polyline, every
amenity's distance to each kept station), but only for stations and amenities that can possibly
qualify. Two grid indexes are built once per DataStore:

* stations and route segments: per route, each segment is listed under the grid cells its bounding
  box covers once widened by buffer_km (plus margin). A station within buffer_km of a segment lies in
  one of that segment's cells, so the stations in those cells are a superset of the ones that pass,
  and the segments listed under a station's cell include every segment within buffer_km of it. Each
  station is measured against those segments with the same point_segment_km arithmetic, cumulative
  lengths and first-minimum tie-break as nearest_on_polyline, so a station that passes gets the
  same (offroute, segment, along) values; one that doesn't is dropped either way.
* amenities: cells of _AMENITY_CELL_DEG around each kept station, then the exact haversine test,
  in DataStore order.

So candidates, their order, distances and amenity lists are identical to the brute force
(tests/retrieval/test_corridor_index.py compares them), at a small fraction of the cost: the
brute force took minutes per request on the whole-US snapshot (85,912 stations x 98,440 amenities).
"""

from __future__ import annotations

import math
from collections import defaultdict

from ..common.geo import haversine_km, nearest_on_polyline, point_segment_km
from ..contracts import Candidate, NearbyAmenity, Route, TripRequest
from ..data import DataStore

_STATION_CELL_DEG = 0.05  # ~5.5 km of latitude
_AMENITY_CELL_DEG = 0.01  # ~1.1 km of latitude
_KM_PER_DEG = 110.0  # slightly below the true ~111.2, so every degree range is a little too wide


def _cell(lat: float, lon: float, size: float) -> tuple[int, int]:
    return math.floor(lat / size), math.floor(lon / size)


def _cells_around(lat: float, lon: float, radius_km: float, size: float) -> list[tuple[int, int]]:
    """Grid cells that cover every point within radius_km of (lat, lon), with margin."""
    dlat = radius_km / _KM_PER_DEG
    cos_lat = max(math.cos(math.radians(min(89.9, abs(lat) + dlat))), 1e-3)
    dlon = radius_km / (_KM_PER_DEG * cos_lat)
    r0, r1 = math.floor((lat - dlat) / size), math.floor((lat + dlat) / size)
    c0, c1 = math.floor((lon - dlon) / size), math.floor((lon + dlon) / size)
    return [(r, c) for r in range(r0, r1 + 1) for c in range(c0, c1 + 1)]


class CorridorRetriever:
    def __init__(self, store: DataStore, buffer_km: float, amenity_radius_m: float, max_candidates: int):
        self.store = store
        self.buffer_km = buffer_km
        self.amenity_radius_m = amenity_radius_m
        self.max_candidates = max_candidates
        self._index_key: tuple | None = None

    # ------------------------------------------------------------------ indexes
    def _ensure_index(self) -> None:
        key = (id(self.store.stations), len(self.store.stations), id(self.store.amenities), len(self.store.amenities))
        if key == self._index_key:
            return
        self._station_order = {sid: i for i, sid in enumerate(self.store.stations)}
        self._station_cells: dict[tuple[int, int], list[str]] = defaultdict(list)
        for sid, st in self.store.stations.items():
            self._station_cells[_cell(st.lat, st.lon, _STATION_CELL_DEG)].append(sid)
        self._amenity_cells: dict[tuple[int, int], list[int]] = defaultdict(list)
        for i, a in enumerate(self.store.amenities):
            self._amenity_cells[_cell(a.lat, a.lon, _AMENITY_CELL_DEG)].append(i)
        self._index_key = key

    def _route_index(self, poly: list[tuple[float, float]]):
        """Per route: grid cell -> indexes of the segments within reach of it, plus the cumulative
        and per-segment lengths, computed exactly as nearest_on_polyline computes them."""
        reach = self.buffer_km * 1.1 + 1.0  # margin over buffer_km for the cell ranges
        cells: dict[tuple[int, int], list[int]] = defaultdict(list)
        seg_len, cumulative, total = [], [], 0.0
        for i in range(len(poly) - 1):
            (alat, alon), (blat, blon) = poly[i], poly[i + 1]
            length = haversine_km(alat, alon, blat, blon)
            seg_len.append(length)
            cumulative.append(total)
            total += length
            # cells covering the segment's bounding box widened by `reach` on every side
            lo_lat, hi_lat = min(alat, blat), max(alat, blat)
            dlat = reach / _KM_PER_DEG
            cos_lat = max(math.cos(math.radians(min(89.9, max(abs(lo_lat), abs(hi_lat)) + dlat))), 1e-3)
            dlon = reach / (_KM_PER_DEG * cos_lat)
            for r in range(math.floor((lo_lat - dlat) / _STATION_CELL_DEG),
                           math.floor((hi_lat + dlat) / _STATION_CELL_DEG) + 1):
                for c in range(math.floor((min(alon, blon) - dlon) / _STATION_CELL_DEG),
                               math.floor((max(alon, blon) + dlon) / _STATION_CELL_DEG) + 1):
                    cells[(r, c)].append(i)
        return cells, seg_len, cumulative

    def _nearest_on_route(self, lat: float, lon: float, poly, segments: list[int], seg_len, cumulative):
        """nearest_on_polyline over the given segments (ascending), same arithmetic and tie-break."""
        best = (math.inf, 0, 0.0)
        for i in segments:
            d, t = point_segment_km((lat, lon), poly[i], poly[i + 1])
            if d < best[0]:
                best = (d, i, cumulative[i] + t * seg_len[i])
        return best

    def _amenities_near(self, lat: float, lon: float) -> list[NearbyAmenity]:
        """Amenities within amenity_radius_m of the station, in DataStore order."""
        idx = sorted(i for c in _cells_around(lat, lon, self.amenity_radius_m / 1000 * 1.1 + 0.05, _AMENITY_CELL_DEG)
                     for i in self._amenity_cells.get(c, ()))
        out = []
        for i in idx:
            a = self.store.amenities[i]
            if (d := haversine_km(lat, lon, a.lat, a.lon) * 1000) <= self.amenity_radius_m:
                out.append(NearbyAmenity(amenity=a, distance_m=d))
        return out

    # ------------------------------------------------------------------ retrieval
    def candidates(self, route: Route, request: TripRequest) -> list[Candidate]:
        self._ensure_index()
        poly = [(p.lat, p.lon) for p in route.geometry]
        out: list[Candidate] = []
        if len(poly) < 2:  # no segment: nearest_on_polyline's own fallback
            stations = [(st, nearest_on_polyline(st.lat, st.lon, poly)) for st in self.store.stations.values()]
        else:
            cells, seg_len, cumulative = self._route_index(poly)
            ids = sorted({sid for c in cells for sid in self._station_cells.get(c, ())},
                         key=self._station_order.__getitem__)
            stations = []
            for sid in ids:
                st = self.store.stations[sid]
                segments = cells[_cell(st.lat, st.lon, _STATION_CELL_DEG)]
                stations.append((st, self._nearest_on_route(st.lat, st.lon, poly, segments, seg_len, cumulative)))
        for st, (off_km, seg, along) in stations:
            if off_km > self.buffer_km:
                continue
            out.append(
                Candidate(
                    station=st,
                    route_index=seg,
                    along_route_km=along,
                    offroute_km=off_km,
                    amenities=self._amenities_near(st.lat, st.lon),
                )
            )
        out.sort(key=lambda c: c.along_route_km)
        return out[: self.max_candidates]
