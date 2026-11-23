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
      - The list is cut to max_candidates (select_distributed, below) before amenity linking.
        Station -> amenity links (same haversine expression, same amenity order) are computed once
        per station per snapshot, from the amenity grid, and memoized.
  * method="brute_force": the original reference implementation, every station against every
    segment and every amenity. Kept for the equivalence tests (tests/retrieval/test_corridor.py).

The cut to max_candidates (#106): taking the first max_candidates by along_route_km let a dense
origin (494 stations within 1 km of downtown Los Angeles) use the whole budget, so a long route
reached the planner with no station past the first kilometre. select_distributed keeps the cap but
spreads it along the route. Only stations the request's vehicle can use (status, access,
connector, power; the planner's station-level checks, injected by the pipeline as `usable`)
compete for the slots. If they fit, all are kept and the other stations fill the rest in route
order. Otherwise the route is split into equal-width distance bins (about bin_km each) and the
slots are shared round-robin across the bins, fastest usable station first in each bin. When the
along-route prefix of usable stations already reaches every bin it is kept, so short routes and
sparse corridors keep their old candidates. Both methods apply the same selection to the same
sorted list.

per_bin (config `max_usable_per_stretch`, #133 review) bounds the planner's search: only the
fastest per_bin usable stations of each bin_km stretch compete at all. Letting every usable
station through (up to 60) made planning time explode (max 356 s on the dev set); 2 per stretch
keeps every dev item and demo trip feasible with max 6.2 s and better plans than the fastest-first
cut. None (the retriever's default) means no bound.

required_extra_per_bin (config `max_required_extra_per_stretch`, #133 / #136) keeps stations the
power-ordered cap would drop but the driver requires. Retrieval mirrors ADR 0003's hard rule only: a
preference is required when it is BOTH `important` and `wheelchair_required`, and only an amenity of
the same category explicitly tagged `wheelchair=yes` satisfies it (unknown and limited never do).
Every other preference, including other `important` ones, stays a ranked preference and never
changes the candidates. After the selection above, a usable station with such an amenity within the
amenity radius is added back, at most required_extra_per_bin per stretch, closest to the route
first. These extras sit on top of max_candidates. A request without a required preference skips
this step, so its candidates are exactly the ones above. `other` preferences are not retained:
whether an `other` amenity satisfies the phrase is the matcher's decision, made after retrieval.

TODO(Manas): a BallTree / R-tree could replace the grids; the equivalence tests stay the contract.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Sequence

from ..common.geo import EARTH_RADIUS_KM, haversine_km, nearest_on_polyline, point_segment_km
from ..contracts import Amenity, AmenityCategory, Candidate, NearbyAmenity, Route, Station, TripRequest
from ..data import DataStore

METHODS = ("indexed", "brute_force")
_KM_PER_DEG = EARTH_RADIUS_KM * math.pi / 180  # same radius as common.geo's projection
_STATION_CELL_DEG = 0.1  # station grid and per-route segment grid share this cell size
_AMENITY_CELL_DEG = 0.05
_EPS_KM = 1e-6  # floating-point slack: pads are a hair wider than the exact bound
_ROUTE_MEMO = 8  # routes whose scan result is kept (relaxation reuses the same route)
DEFAULT_BIN_KM = 25.0  # distance-bin width of the capped selection (#106)

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


def polyline_km(poly: Sequence[tuple[float, float]]) -> float:
    """Length of the route geometry, summed segment by segment as along_route_km is."""
    return sum(haversine_km(*poly[i], *poly[i + 1]) for i in range(len(poly) - 1))


def max_power_kw(station) -> float:
    """A station's fastest port (unknown power counts as 0); the within-bin order of select_distributed."""
    return max((p.power_kw or 0.0 for p in station.ports), default=0.0)


def select_distributed(
    along_km: Sequence[float],
    max_candidates: int,
    route_km: float,
    bin_km: float = DEFAULT_BIN_KM,
    power_kw: Sequence[float] | None = None,
    usable: Sequence[bool] | None = None,
    per_bin: int | None = None,
) -> list[int]:
    """Indices into an along_route_km-sorted list to keep under the cap, ascending (#106).

    `usable[i]` says whether the request's vehicle can ever stop at entry i (status, access,
    connector, power: the planner's station-level hard constraints, passed in by the pipeline).
    Only usable entries compete for the slots, so an unusable station can never displace a usable
    one (#133 review):

    * at most max_candidates usable entries: all of them are kept, and the remaining slots go to
      the other entries in along-route order. When every usable entry is already among the first
      max_candidates, this is exactly that prefix (the pre-#106 cut);
    * more usable entries than slots: they are spread along the route (_spread). Unusable entries
      are dropped; the filter would reject them anyway.

    Without `usable` (no request or vehicle), every entry counts as usable.

    `per_bin` bounds the planner's search: at most that many usable entries per bin_km stretch
    (fastest first) compete at all, so a dense stretch of compatible chargers cannot hand the
    planner dozens of near-equivalent stops.
    """
    n = len(along_km)
    if max_candidates <= 0:
        return []
    if per_bin is None and n <= max_candidates:
        return list(range(n))
    good = [i for i in range(n) if usable is None or usable[i]]
    if per_bin is not None:
        good = _per_bin_quota(good, along_km, route_km, bin_km, power_kw, per_bin)
    if len(good) <= max_candidates:
        chosen = set(good)
        rest = [i for i in range(n) if i not in chosen and (usable is None or not usable[i])]
        if per_bin is not None:  # a usable entry over its stretch's quota stays out
            rest = [i for i in rest if usable is not None]
        return sorted(good + rest[: max_candidates - len(good)])
    picked = _spread(
        [along_km[i] for i in good],
        max_candidates,
        route_km,
        bin_km,
        None if power_kw is None else [power_kw[i] for i in good],
    )
    return [good[j] for j in picked]


def _per_bin_quota(
    idx: list[int],
    along_km: Sequence[float],
    route_km: float,
    bin_km: float,
    power_kw: Sequence[float] | None,
    per_bin: int,
) -> list[int]:
    """The fastest `per_bin` of `idx` in each bin_km stretch of the route, ascending."""
    n_bins = max(1, math.ceil(route_km / bin_km)) if route_km > 0 else 1
    width = route_km / n_bins if route_km > 0 else 1.0
    bins: dict[int, list[int]] = {}
    for i in idx:
        bins.setdefault(min(n_bins - 1, max(0, int(along_km[i] / width))), []).append(i)
    keep: list[int] = []
    for b in bins.values():
        if power_kw is not None:
            b = sorted(b, key=lambda i: (-power_kw[i], i))
        keep += b[:per_bin]
    return sorted(keep)


RequiredPreference = tuple[AmenityCategory, bool]  # (category, wheelchair_required)


def required_preferences(request: TripRequest | None) -> list[RequiredPreference]:
    """The request's hard amenity requirements (ADR 0003): `important` AND `wheelchair_required`.
    Other preferences, including other `important` ones, stay soft and are not retained. `other`
    is excluded because only the matcher can decide whether an `other` amenity satisfies it."""
    if request is None:
        return []
    return [
        (p.category, p.wheelchair_required)
        for p in request.amenities
        if p.priority == "important" and p.wheelchair_required and p.category != AmenityCategory.OTHER
    ]


def satisfies_required(amenities: Sequence[Amenity], required: Sequence[RequiredPreference]) -> bool:
    """True if one of the amenities satisfies a required preference: same category and, when
    wheelchair access is required, explicitly `wheelchair=yes` (unknown never counts)."""
    return any(
        a.category == category and (not wheelchair or a.wheelchair == "yes")
        for a in amenities
        for category, wheelchair in required
    )


def select_required_extras(
    along_km: Sequence[float],
    offroute_km: Sequence[float],
    keep: Sequence[int],
    route_km: float,
    required: Sequence[bool],
    per_bin: int,
    bin_km: float = DEFAULT_BIN_KM,
    usable: Sequence[bool] | None = None,
) -> list[int]:
    """Indices (ascending, not in `keep`) of usable entries that satisfy a required preference,
    at most per_bin per bin_km stretch, closest to the route first (ties: list order)."""
    if per_bin <= 0:
        return []
    kept = set(keep)
    n_bins = max(1, math.ceil(route_km / bin_km)) if route_km > 0 else 1
    width = route_km / n_bins if route_km > 0 else 1.0
    bins: dict[int, list[int]] = {}
    for i in range(len(along_km)):
        if i in kept or not required[i] or (usable is not None and not usable[i]):
            continue
        bins.setdefault(min(n_bins - 1, max(0, int(along_km[i] / width))), []).append(i)
    extra: list[int] = []
    for b in bins.values():
        extra += sorted(b, key=lambda i: (offroute_km[i], i))[:per_bin]
    return sorted(extra)


def _spread(
    along_km: Sequence[float], max_candidates: int, route_km: float, bin_km: float, power_kw: Sequence[float] | None
) -> list[int]:
    """max_candidates indices (ascending) of an along-sorted list longer than max_candidates.

    The route is split into n = clamp(ceil(route_km / bin_km), 1, max_candidates) bins of equal
    width. If the first max_candidates entries already include one from every non-empty bin, that
    prefix is kept. Otherwise the budget is filled round-robin across the non-empty bins, so every
    stretch of the route that has stations keeps some. Within a bin, stations go fastest port first
    (power_kw, descending), then by position. Deterministic: ties fall back to the list order.
    """
    n_bins = max(1, min(max_candidates, math.ceil(route_km / bin_km))) if route_km > 0 else 1
    width = route_km / n_bins if route_km > 0 else 1.0
    bins: list[list[int]] = [[] for _ in range(n_bins)]
    for i, along in enumerate(along_km):
        bins[min(n_bins - 1, max(0, int(along / width)))].append(i)
    filled = [b for b in bins if b]
    if all(b[0] < max_candidates for b in filled):  # the prefix already spans the route
        return list(range(max_candidates))
    if power_kw is not None:
        filled = [sorted(b, key=lambda i: (-power_kw[i], i)) for b in filled]
    keep: list[int] = []
    depth = 0
    while len(keep) < max_candidates:
        for b in filled:
            if depth < len(b):
                keep.append(b[depth])
                if len(keep) == max_candidates:
                    break
        depth += 1
    return sorted(keep)


class CorridorRetriever:
    def __init__(
        self,
        store: DataStore,
        buffer_km: float,
        amenity_radius_m: float,
        max_candidates: int,
        method: str = "indexed",
        bin_km: float = DEFAULT_BIN_KM,
        usable: Callable[[Station, TripRequest], bool] | None = None,
        per_bin: int | None = None,
        required_extra_per_bin: int | None = None,
    ):
        if method not in METHODS:
            raise ValueError(f"method must be one of {METHODS}, got {method!r}")
        self.store = store
        self.buffer_km = buffer_km
        self.amenity_radius_m = amenity_radius_m
        self.max_candidates = max_candidates
        self.bin_km = bin_km
        # Station-level hard constraints for the capped selection, injected by the pipeline (it wraps
        # the planner's filter); retrieval itself knows nothing about vehicles or connectors.
        self.usable = usable
        self.per_bin = per_bin  # at most this many usable candidates per bin_km stretch (None: no bound)
        # extra stations per stretch kept because they satisfy a required amenity (None/0: off)
        self.required_extra_per_bin = required_extra_per_bin
        self.method = method
        self._station_grid: dict[Cell, list[int]] | None = None  # built lazily, rebuilt if the store changes
        self._stations: list = []
        self._amenity_grid: dict[Cell, list[int]] | None = None
        self._amenity_links: dict[str, list[tuple[int, float]]] = {}  # station_id -> [(amenity idx, m)]
        self._route_scans: dict[tuple, list[tuple[float, int, int, float]]] = {}  # insertion-ordered memo
        self._store_signature: tuple | None = None  # what the grids and memos were built from

    def candidates(self, route: Route, request: TripRequest | None) -> list[Candidate]:
        if self.method == "brute_force":
            return self._candidates_brute_force(route, request)
        return self._candidates_indexed(route, request)

    def _usable_flags(self, stations: Sequence[Station], request: TripRequest | None) -> list[bool] | None:
        if self.usable is None or request is None:
            return None
        return [self.usable(st, request) for st in stations]

    def _with_required_extras(
        self,
        keep: list[int],
        along_km: Sequence[float],
        offroute_km: Sequence[float],
        route_km: float,
        usable: Sequence[bool] | None,
        request: TripRequest | None,
        amenities_of: Callable[[int], Sequence[Amenity]],
    ) -> list[int]:
        """`keep` plus the stations kept for a required amenity (see the module docstring).
        Unchanged when the request has no required preference or the bound is off."""
        required_prefs = required_preferences(request)
        if not self.required_extra_per_bin or not required_prefs:
            return keep
        kept = set(keep)
        required = [
            i not in kept and (usable is None or usable[i]) and satisfies_required(amenities_of(i), required_prefs)
            for i in range(len(along_km))
        ]
        extra = select_required_extras(
            along_km, offroute_km, keep, route_km, required, self.required_extra_per_bin, self.bin_km, usable
        )
        return sorted(keep + extra)

    # ------------------------------------------------------------------ reference implementation
    def _candidates_brute_force(self, route: Route, request: TripRequest | None = None) -> list[Candidate]:
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
        along, route_km = [c.along_route_km for c in out], polyline_km(poly)
        usable = self._usable_flags([c.station for c in out], request)
        keep = select_distributed(
            along,
            self.max_candidates,
            route_km,
            self.bin_km,
            [max_power_kw(c.station) for c in out],
            usable,
            self.per_bin,
        )
        keep = self._with_required_extras(
            keep,
            along,
            [c.offroute_km for c in out],
            route_km,
            usable,
            request,
            lambda i: [n.amenity for n in out[i].amenities],
        )
        return [out[i] for i in keep]

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

    def _check_store(self) -> None:
        """Drop the grids and memos if the store's station or amenity collections were replaced or
        resized since they were built (nothing does this today; it keeps the index honest if it does)."""
        signature = (
            id(self.store.stations),
            len(self.store.stations),
            id(self.store.amenities),
            len(self.store.amenities),
        )
        if signature != self._store_signature:
            self._station_grid, self._stations, self._amenity_grid = None, [], None
            self._amenity_links.clear()
            self._route_scans.clear()
            self._store_signature = signature

    def _candidates_indexed(self, route: Route, request: TripRequest | None = None) -> list[Candidate]:
        self._check_store()
        poly = [(p.lat, p.lon) for p in route.geometry]
        if len(poly) < 2:  # degenerate route: nearest_on_polyline has no segment to measure
            return self._candidates_brute_force(route, request)
        key = (route.route_id, len(poly), hash(tuple(poly)))
        found = self._route_scans.get(key)
        if found is None:
            found = self._scan(poly)
            self._route_scans[key] = found
            while len(self._route_scans) > _ROUTE_MEMO:
                del self._route_scans[next(iter(self._route_scans))]

        along, route_km = [f[0] for f in found], polyline_km(poly)
        usable = self._usable_flags([self._stations[f[1]] for f in found], request)
        keep = select_distributed(
            along,
            self.max_candidates,
            route_km,
            self.bin_km,
            [max_power_kw(self._stations[f[1]]) for f in found],
            usable,
            self.per_bin,
        )
        keep = self._with_required_extras(
            keep,
            along,
            [f[3] for f in found],
            route_km,
            usable,
            request,
            lambda i: [self.store.amenities[ai] for ai, _ in self._links_for(self._stations[found[i][1]])],
        )
        out = []
        for along, si, seg, off_km in (found[i] for i in keep):
            st = self._stations[si]
            nearby = [NearbyAmenity(amenity=self.store.amenities[ai], distance_m=d) for ai, d in self._links_for(st)]
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
