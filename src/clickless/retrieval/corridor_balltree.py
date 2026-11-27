"""BallTree prototype of the corridor scan, for benchmarking only (issue #190). Owner: Manas.

Not wired into the pipeline. Production uses CorridorRetriever(method="indexed"), and the grid
remains the contract. This class exists so scripts/benchmark_corridor_index.py can compare the two,
and so tests/retrieval/test_corridor.py can show the alternative gives identical candidates.

What changes: only how a station finds the route segments it must be measured against.

  * Grid (production): every segment is registered in each 0.1-degree cell its padded bounding box
    touches, and a station is compared with every segment registered in its cell.
  * BallTree (this module): stations sit in a sklearn BallTree over (lat, lon) degrees with the
    Chebyshev metric. A Chebyshev ball is an axis-aligned square, so for each segment one query
    returns every station inside a square that contains the segment's padded bounding box. The
    stations are then filtered to the box itself, so a station is compared only with segments
    whose padded box contains it.

The pads are the same _segment_pads as the grid, so the same proof applies: a station outside a
segment's padded box is farther than the buffer from that segment as point_segment_km measures it.
Every segment within the buffer is therefore still compared, in ascending order with the same
strict-< tie rule, which gives the same best segment, along_route_km and offroute_km as the grid
and the brute force. Everything after the scan (sorting, select_distributed, required extras,
amenity links) is inherited unchanged.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from sklearn.neighbors import BallTree

from ..common.geo import haversine_km, point_segment_km
from .corridor import CorridorRetriever, _segment_pads


class BallTreeCorridorRetriever(CorridorRetriever):
    """CorridorRetriever whose corridor scan uses a station BallTree instead of the grids."""

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("method", "indexed")
        if kwargs["method"] != "indexed":
            raise ValueError("BallTreeCorridorRetriever only replaces the indexed scan")
        super().__init__(*args, **kwargs)
        self._station_tree: BallTree | None = None
        self._station_coords: np.ndarray | None = None

    def _check_store(self) -> None:
        before = self._store_signature
        super()._check_store()
        if self._store_signature != before:
            self._station_tree, self._station_coords = None, None

    def _build_station_tree(self) -> BallTree:
        if self._station_tree is None:
            self._stations = list(self.store.stations.values())  # store order = brute-force order
            coords = np.array([(st.lat, st.lon) for st in self._stations], dtype=float).reshape(-1, 2)
            self._station_coords = coords
            self._station_tree = BallTree(coords, metric="chebyshev")
        return self._station_tree

    def _segments_by_station(self, poly: Sequence[tuple[float, float]]) -> dict[int, list[int]]:
        """station index -> ascending segment indices whose padded bounding box contains it."""
        tree = self._build_station_tree()
        coords = self._station_coords
        assert coords is not None
        n_seg = len(poly) - 1
        if n_seg <= 0 or len(coords) == 0:
            return {}
        lo = np.empty((n_seg, 2))
        hi = np.empty((n_seg, 2))
        for i in range(n_seg):
            a, b = poly[i], poly[i + 1]
            lat_pad, lon_pad = _segment_pads(a, b, self.buffer_km)
            lo[i] = (min(a[0], b[0]) - lat_pad, min(a[1], b[1]) - lon_pad)
            hi[i] = (max(a[0], b[0]) + lat_pad, max(a[1], b[1]) + lon_pad)
        centres = (lo + hi) / 2
        # Chebyshev radius = the larger half-width, so the square contains the box (a superset);
        # the exact box test below keeps only the stations the grid's proof says may be in range.
        radii = np.max((hi - lo) / 2, axis=1)
        hits = tree.query_radius(centres, r=radii)
        by_station: dict[int, list[int]] = {}
        for i in range(n_seg):  # ascending, so every station's list is in segment order
            idx = hits[i]
            if idx.size == 0:
                continue
            pts = coords[idx]
            inside = np.all((pts >= lo[i]) & (pts <= hi[i]), axis=1)
            for si in idx[inside].tolist():
                by_station.setdefault(si, []).append(i)
        return by_station

    def _scan(self, poly: Sequence[tuple[float, float]]) -> list[tuple[float, int, int, float]]:
        """(along_km, station index, segment, offroute_km) of every corridor station, sorted."""
        seg_len, cumulative, acc = [], [], 0.0
        for i in range(len(poly) - 1):
            length = haversine_km(*poly[i], *poly[i + 1])
            seg_len.append(length)
            cumulative.append(acc)
            acc += length

        found: list[tuple[float, int, int, float]] = []
        for si, segments in self._segments_by_station(poly).items():
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
