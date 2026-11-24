"""Label-setting oracle for model-relative feasibility (proposal section V). Owner: Ian.

Search state (label) at a corridor station: (route position, elapsed time, SoC, path).
Expand to every later compatible station or the destination when the leg is feasible under the
documented model; at each station consider a small set of departure SoCs. Prune dominated labels.
Uses the same frozen inputs (DataStore, cached routes) but none of the planner's code: it must not
import clickless.planning, clickless.ranking, clickless.retrieval or clickless.pipeline
(tests/evaluation/test_oracle_independence.py). Design, inputs and every documentation gap with
the decision taken: docs/EVALUATION_ORACLE.md.

Outputs per request:
  * is_feasible(request)      -> FeasibilityResult (exact; reason code when infeasible)
  * pareto_plans(request)     -> non-dominated plans over (total time, detour, charge time)
  * check_itinerary(request, itinerary) -> CheckReport listing EVERY violated hard constraint
  * is_valid(itinerary, request) -> bool

Required accessible restroom (ADR 0003 question 3, #162): when the request has a restroom
preference with priority "important" AND wheelchair_required, a plan is valid only if one of its
charging stops has a datastore amenity of category restroom tagged wheelchair == "yes" (unknown and
limited never count) within amenity_radius_m (configs/retrieval.yaml) straight-line. Every other
amenity preference stays soft and never changes an oracle answer.

Disagreements with the planner are findings. Never change this oracle to match the planner: fix
the documentation first, then whichever implementation is wrong.

Speed (OracleConfig.method): "indexed" (default) finds corridor stations through a grid index
(_corridor_index.py) and caches per-route geometry and station projections, and the per-request
problem (context + candidate nodes), across is_feasible, pareto_plans and check_itinerary.
"exhaustive" is the original path, locating every station on every segment with no caching; it
is kept as the reference, and tests/evaluation assert both give identical results.

pareto_plans results are cached per (request, settings) and shared with with_overrides copies
(indexed only; callers get copies). The search stops after max_labels labels and returns complete=False
with incomplete_reason="max_labels": deterministic, so the same input gives the same front and flag.
pareto_time_budget_s is a wall-clock safety net only ("time_budget"); if it fires the result depends on
machine load, and planning metrics report it as oracle_nondeterministic_cutoff.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import math
import time
from bisect import bisect_right
from collections import Counter, OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from clickless.contracts import (
    AmenityCategory,
    Itinerary,
    Place,
    PlanResult,
    Route,
    Station,
    StationStatus,
    TripRequest,
    Vehicle,
    VerificationReport,
    Violation,
)

from . import _geometry as geo
from ._corridor_index import RouteIndex
from .energy_reference import (
    PortChoice,
    access_km,
    allowed_connectors,
    charge_minutes,
    detour_minutes,
    is_charge_time_excluded,
    leg_soc_drop_pct,
    meets_min_power,
    override_has_no_overlap,
    peak_kw,
    port_compatible,
    port_power_kw,
    station_rejection,
    usable_ports,
)

EPS = 1e-9
EXCLUDED_STATUSES = frozenset({StationStatus.TEMPORARILY_UNAVAILABLE, StationStatus.PLANNED})
RouteSource = Callable[[Place, Place], "Route | None"]
DetourScope = Literal["stop", "total"]
Method = Literal["indexed", "exhaustive"]
METHODS = ("indexed", "exhaustive")
MAX_CACHED_REQUESTS = 16
MAX_CACHED_FRONTS = 512  # Pareto results kept (items x settings variants in one evaluation run)


class OracleUnavailable(ValueError):
    """The request cannot be evaluated (missing fields, unresolved place, no route, unknown vehicle)."""


# --------------------------------------------------------------------------- routes


class FileRouteSource:
    """Reads cached `Route` JSON files and matches them on origin/destination coordinates.

    The file-naming scheme of routes/ is undocumented (gap 16), so names are not used.
    """

    def __init__(self, routes_dir: str | Path, tol_deg: float = 0.01) -> None:
        self.routes_dir = Path(routes_dir)
        self.tol_deg = tol_deg
        self._routes: list[Route] | None = None

    def _all(self) -> list[Route]:
        if self._routes is None:
            files = sorted(self.routes_dir.glob("*.json")) if self.routes_dir.is_dir() else []
            self._routes = [Route.model_validate(json.loads(f.read_text(encoding="utf-8"))) for f in files]
        return self._routes

    def __call__(self, origin: Place, destination: Place) -> Route | None:
        def near(a, b: Place) -> bool:
            return abs(a.lat - b.lat) <= self.tol_deg and abs(a.lon - b.lon) <= self.tol_deg

        return next((r for r in self._all() if near(r.origin, origin) and near(r.destination, destination)), None)


# --------------------------------------------------------------------------- configuration and results


@dataclass(frozen=True)
class OracleConfig:
    energy: Mapping[str, Any]  # configs/energy.yaml
    corridor_buffer_km: float = 8.0  # configs/retrieval.yaml corridor_buffer_km; math.inf = no buffer
    efficiency_penalty: float = 0.0  # 0.15 for the robust run
    unknown_power_kw: Mapping[str, float] | None = None  # per-level override of charging_model.unknown_power_kw
    detour_scope: DetourScope = "stop"  # max_detour_min per stop (default) or for the whole trip (gap 2)
    soc_grid_step_pct: float = 1.0  # departure-SoC grid of the Pareto search
    soc_tol_pct: float = 0.5  # tolerance of SoC checks in check_itinerary
    detour_tol_min: float = 1.0  # tolerance of detour checks in check_itinerary
    route_distance_tol: float = 0.05  # relative tolerance of summed leg km
    continuity_tol_km: float = 0.5
    pareto_tol_min: float = 0.5  # tolerance when comparing a planner plan with the oracle front
    max_labels: int = 40_000  # deterministic search limit (configs/evaluation.yaml oracle.max_labels)
    pareto_time_budget_s: float | None = 600.0  # wall-clock SAFETY NET per pareto_plans call; None = none
    diagnostic_buffer_km: float = 50.0  # corridor-buffer diagnostic's wide buffer (configs/evaluation.yaml)
    # Planner POLICY, not a constraint (gap 11): after a charging stop, depart with enough to arrive at
    # the next node this much above its floor (configs/energy.yaml charge_buffer_soc_pct). 0 (the
    # default) is the strict oracle; a non-zero value gives the policy-adjusted front that
    # tradeoffs.pareto_validity compares with. Never applied to is_feasible or check_itinerary.
    departure_buffer_pct: float = 0.0
    method: Method = "indexed"  # speed only: "exhaustive" is the reference path, same results
    # configs/retrieval.yaml amenity_radius_m: straight-line radius of the required accessible
    # restroom around a charging stop (#162). Used only when requires_accessible_restroom(request).
    amenity_radius_m: float = 400.0

    def __post_init__(self) -> None:
        if self.method not in METHODS:
            raise ValueError(f"method must be one of {METHODS}, got {self.method!r}")

    @property
    def charging_model(self) -> Mapping[str, Any]:
        return self.energy["charging_model"]

    @property
    def detour_model(self) -> Mapping[str, Any]:
        return self.energy["placeholder_detour"]

    @property
    def reserve(self) -> float:
        return float(self.energy["reserve_soc_pct"])

    @property
    def cap(self) -> float:
        return float(self.energy["charge_target_max_soc_pct"])

    def floor_dest(self, request: TripRequest) -> float:
        wanted = request.arrival_soc_pct_min
        if wanted is None:
            wanted = float(self.energy["default_arrival_soc_pct"])
        return max(self.reserve, float(wanted))


@dataclass
class FeasibilityResult:
    feasible: bool
    reason: str | None = None  # None when feasible
    detail: str = ""
    evaluable: bool = True  # False: missing fields / unresolved place / no route
    excluded: dict[str, int] = field(default_factory=dict)  # stations excluded, by reason
    charge_time_excluded: bool = False  # ADR 0009 (LEAF)
    witness: list[str] = field(default_factory=list)  # station ids of one feasible plan
    unknown_status_station_ids: list[str] = field(default_factory=list)  # in the witness

    def __bool__(self) -> bool:
        """`if oracle.is_feasible(request):` means feasible (a dataclass is otherwise always truthy)."""
        return self.feasible


@dataclass(frozen=True)
class OracleStop:
    station_id: str
    connector: str
    port_power_kw: float | None
    power_estimated: bool
    along_km: float
    detour_min: float
    arrive_soc_pct: float
    depart_soc_pct: float
    charge_min: float


@dataclass(frozen=True)
class OraclePlan:
    stops: tuple[OracleStop, ...]
    total_time_min: float
    total_detour_min: float
    total_charge_min: float
    arrival_soc_pct: float
    unknown_status_station_ids: tuple[str, ...]

    @property
    def objectives(self) -> tuple[float, float, float]:
        return (self.total_time_min, self.total_detour_min, self.total_charge_min)

    @property
    def station_ids(self) -> tuple[str, ...]:
        return tuple(s.station_id for s in self.stops)


@dataclass
class ParetoResult:
    plans: list[OraclePlan]
    complete: bool  # False when the search stopped early (the front may be missing plans)
    feasibility: FeasibilityResult | None = None  # set when the request was rejected before searching
    incomplete_reason: str | None = None  # "time_budget" or "max_labels" when complete is False
    n_labels: int = 0  # labels inserted by the search (deterministic for a given input and config)


@dataclass
class CheckReport:
    ok: bool
    violations: list[Violation]
    notes: list[str] = field(default_factory=list)  # claim mismatches that are not hard violations
    objectives: tuple[float, float, float] | None = None  # (time, detour, charge) under the oracle model
    uses_unknown_power: bool = False
    # The same checks with no tolerance (the search's own EPS): False for a plan that passes only
    # because of soc_tol_pct / detour_tol_min. Findings that compare with the search use this.
    strict_ok: bool = True
    strict_violations: list[str] = field(default_factory=list)  # codes failed only without tolerance

    def to_verification_report(self) -> VerificationReport:
        return VerificationReport(ok=self.ok, violations=list(self.violations))


# --------------------------------------------------------------------------- internal problem


@dataclass(frozen=True)
class _Node:
    kind: Literal["origin", "station", "destination"]
    along_km: float
    access_km: float = 0.0
    detour_min: float = 0.0
    station: Station | None = None
    port: PortChoice | None = None


@dataclass
class _Context:
    request: TripRequest
    vehicle: Vehicle
    route: Route
    poly: list[tuple[float, float]]
    scale: float  # road km per straight-line km along the geometry
    start: float
    floor_dest: float
    route_key: tuple = ()  # identifies the route geometry in the RouteIndex


@dataclass
class _Problem:
    ctx: _Context
    nodes: list[_Node]
    excluded: dict[str, int]
    penalty: float
    no_overlap_detail: str | None = None  # set when connectors_override shares nothing with the vehicle
    # Required accessible restroom (#162): None when the request does not require one (the search is
    # then exactly the one without the rule); otherwise, per node, whether a stop there satisfies it.
    access_ok: list[bool] | None = None

    def drop(self, i: int, j: int) -> float:
        a, b = self.nodes[i], self.nodes[j]
        km = a.access_km + max(0.0, b.along_km - a.along_km) + b.access_km
        return leg_soc_drop_pct(km, self.ctx.vehicle, self.penalty)


class _Label:
    __slots__ = ("node", "time", "soc", "detour", "charge", "parent", "stop", "unk")

    def __init__(self, node, time, soc, detour, charge, parent, stop, unk=0):
        self.node, self.time, self.soc, self.detour, self.charge = node, time, soc, detour, charge
        self.parent, self.stop = parent, stop  # stop: (node index, arrive, depart, charge_min) of the PREVIOUS node
        self.unk = unk  # stops at stations whose status is UNKNOWN: breaks exact ties only (see _insert_pref)

    def key(self) -> tuple[float, float, float, float]:
        return (self.time, -self.soc, self.detour, self.charge)


def _weakly_dominates(a: tuple[float, ...], b: tuple[float, ...], tol: float = EPS) -> bool:
    """a <= b in every (minimised) component, within tol."""
    return all(x <= y + tol for x, y in zip(a, b, strict=True))


class _Front:
    """A node's label front with the same semantics as _insert(front, label, _Label.key), faster.

    `labels` keeps _insert's order (survivors in insertion order, the new label appended), so the
    search expands labels in exactly the reference order. "Is the new key weakly dominated by some
    label?" does not depend on order, so it is answered from a copy sorted by time: only labels with
    time <= new time + EPS can dominate, and the four comparisons are the reference's own
    (x <= y + EPS, with y + EPS computed from the same floats).
    """

    __slots__ = ("labels", "times", "keys", "hint")

    def __init__(self) -> None:
        self.labels: list[_Label] = []
        self.times: list[float] = []  # sorted
        self.keys: list[tuple[float, float, float, float]] = []  # aligned with times
        self.hint: tuple[float, float, float, float] | None = None  # the key that last dominated a candidate

    def dominated(self, t0: float, t1: float, t2: float, t3: float, key: tuple, u: int) -> bool:
        """Some label beats the candidate (key, u) under _beats, where t_c = key_c + EPS.

        Stored keys are (time, -soc, detour, charge, unk). The answer does not depend on the scan
        order, so the label that dominated the previous candidate (often the same one) is tried first."""
        k0, k1, k2, k3 = key

        def beats(o) -> bool:
            if not (o[0] <= t0 and o[1] <= t1 and o[2] <= t2 and o[3] <= t3):
                return False
            # exact tie (the candidate also weakly dominates o) and o relies on more UNKNOWN stations
            return not (o[4] > u and k0 <= o[0] + EPS and k1 <= o[1] + EPS and k2 <= o[2] + EPS and k3 <= o[3] + EPS)

        h = self.hint
        if h is not None and beats(h):
            return True
        for o in self.keys[: bisect_right(self.times, t0)]:
            if beats(o):
                self.hint = o
                return True
        return False

    def add(self, label: _Label, key: tuple[float, float, float, float]) -> None:
        """Insert a label that no current label beats; drop the labels it beats (same test as
        _insert_pref: _beats with key_c <= o_c + EPS for every component)."""
        k0, k1, k2, k3 = key
        u = label.unk
        stored = (k0, k1, k2, k3, u)

        def gone_key(o) -> bool:
            if not (k0 <= o[0] + EPS and k1 <= o[1] + EPS and k2 <= o[2] + EPS and k3 <= o[3] + EPS):
                return False
            return not (u > o[4] and o[0] <= k0 + EPS and o[1] <= k1 + EPS and o[2] <= k2 + EPS and o[3] <= k3 + EPS)

        if self.labels:
            keys = self.keys
            gone = [o for o in keys if gone_key(o)]
            if gone:
                drop = {id(o) for o in gone}
                self.keys = [o for o in keys if id(o) not in drop]  # stays sorted
                self.times = [o[0] for o in self.keys]
                self.labels = [o for o in self.labels if not gone_key((o.time, -o.soc, o.detour, o.charge, o.unk))]
                if self.hint is not None and id(self.hint) in drop:
                    self.hint = None
        self.labels.append(label)
        pos = bisect_right(self.times, k0)
        self.times.insert(pos, k0)
        self.keys.insert(pos, stored)


def _insert(front: list, item, key: Callable) -> bool:
    k = key(item)
    if any(_weakly_dominates(key(o), k) for o in front):
        return False
    front[:] = [o for o in front if not _weakly_dominates(k, key(o))]
    front.append(item)
    return True


def _beats(ok: tuple, ou: int, k: tuple, u: int) -> bool:
    """Does (ok, ou) remove (k, u) from a front? Weak dominance within EPS, except that on an exact
    tie (each weakly dominates the other) the side relying on fewer UNKNOWN-status stations wins,
    so a station whose ID happens to sort first never beats an equivalent AVAILABLE one."""
    return _weakly_dominates(ok, k) and not (ou > u and _weakly_dominates(k, ok))


def _insert_pref(front: list, item, key: Callable, unk: Callable) -> bool:
    """_insert with the unknown-status tie-break of _beats."""
    k, u = key(item), unk(item)
    if any(_beats(key(o), unk(o), k, u) for o in front):
        return False
    front[:] = [o for o in front if not _beats(k, u, key(o), unk(o))]
    front.append(item)
    return True


def _label_unk(label: _Label) -> int:
    return label.unk


def _final_front(plans: list[OraclePlan]) -> list[OraclePlan]:
    """Non-dominated plans over (time, detour, charge); an exact tie keeps the plan with fewer
    UNKNOWN-status stops. Sorted by objectives."""
    final: list[OraclePlan] = []
    for plan in sorted(plans, key=lambda x: (x.objectives, len(x.unknown_status_station_ids))):
        _insert_pref(final, plan, lambda x: x.objectives, lambda x: len(x.unknown_status_station_ids))
    return sorted(final, key=lambda x: x.objectives)


def requires_accessible_restroom(request: TripRequest) -> bool:
    """A restroom preference that is both important and wheelchair_required is a hard constraint
    (ADR 0003 question 3). nice_to_have + wheelchair, and other important amenities, stay soft."""
    return any(
        a.category == AmenityCategory.RESTROOM and a.priority == "important" and a.wheelchair_required
        for a in request.amenities
    )


# --------------------------------------------------------------------------- the oracle


class LabelSettingOracle:
    def __init__(
        self,
        store,
        cfg: OracleConfig | Mapping[str, Any],
        route_source: RouteSource | None = None,
        *,
        _index: RouteIndex | None = None,
        _fronts: OrderedDict | None = None,
    ) -> None:
        self.store = store
        # Backwards compatible with the stub signature LabelSettingOracle(store, energy_cfg).
        self.cfg = cfg if isinstance(cfg, OracleConfig) else OracleConfig(energy=cfg)
        self.route_source: RouteSource = route_source or FileRouteSource(store.routes_dir)
        self._index = _index or RouteIndex()  # per-route work, shared with with_overrides copies
        self._requests: OrderedDict[str, Any] = OrderedDict()  # per-request context / problem
        self._requests_signature: tuple | None = None
        self._fronts: OrderedDict = _fronts if _fronts is not None else OrderedDict()  # shared Pareto cache
        self._restroom_signature: tuple | None = None
        self._restroom_ok: dict[str, bool] = {}  # station_id -> accessible restroom within the radius
        self._accessible_restrooms: list[tuple[float, float]] = []

    @property
    def energy_cfg(self) -> Mapping[str, Any]:
        return self.cfg.energy

    @classmethod
    def from_config(cls, cfg: Mapping[str, Any], store=None, route_source: RouteSource | None = None, **overrides):
        """Build from a merged config (load_config("A")): energy + retrieval.corridor_buffer_km."""
        if store is None:
            from clickless.data.loaders import DataStore

            store = DataStore.from_config(dict(cfg))
        ev = (cfg.get("evaluation") or {}).get("oracle", {})
        budget = ev.get("pareto_time_budget_s", OracleConfig.pareto_time_budget_s)
        max_labels = ev.get("max_labels", OracleConfig.max_labels)
        oc = OracleConfig(
            energy=cfg["energy"],
            corridor_buffer_km=float(cfg["retrieval"]["corridor_buffer_km"]),
            pareto_time_budget_s=None if budget is None else float(budget),
            max_labels=int(max_labels),
            diagnostic_buffer_km=float(ev.get("diagnostic_buffer_km", OracleConfig.diagnostic_buffer_km)),
            amenity_radius_m=float(cfg["retrieval"]["amenity_radius_m"]),
        )
        return cls(store, dataclasses.replace(oc, **overrides), route_source)

    def with_overrides(self, **overrides) -> LabelSettingOracle:
        """Same store and routes, different model settings (robust / sensitivity / diagnostic runs)."""
        return LabelSettingOracle(
            self.store,
            dataclasses.replace(self.cfg, **overrides),
            self.route_source,
            _index=self._index,
            _fronts=self._fronts,
        )

    # ---------------------------------------------------------------- per-request cache (indexed only)

    def _cached(self, kind: str, request: TripRequest, build: Callable[[], Any]) -> Any:
        """Memoize build() per (kind, request) while the store's stations and vehicles are the same
        objects. Public results are never the cached objects themselves (see is_feasible)."""
        if self.cfg.method == "exhaustive":
            return build()
        store = self.store
        signature = (
            id(store.stations),
            len(store.stations),
            id(store.vehicles),
            len(store.vehicles),
            id(store.amenities),
            len(store.amenities),
        )
        if signature != self._requests_signature:
            self._requests.clear()
            self._requests_signature = signature
        key = f"{kind}:{request.model_dump_json()}"
        if key in self._requests:
            self._requests.move_to_end(key)
            return self._requests[key]
        value = self._requests[key] = build()
        while len(self._requests) > MAX_CACHED_REQUESTS:
            self._requests.popitem(last=False)
        return value

    # ---------------------------------------------------------------- inputs

    def _resolve(self, place: Place | None) -> Place | None:
        if place is None:
            return None
        if place.resolved:
            return place
        found = self.store.geocode(place.text)
        return found if found.resolved else None

    def _context(self, request: TripRequest) -> _Context | FeasibilityResult:
        return self._cached("context", request, lambda: self._build_context(request))

    def _build_context(self, request: TripRequest) -> _Context | FeasibilityResult:
        def not_evaluable(reason: str, detail: str) -> FeasibilityResult:
            return FeasibilityResult(False, reason, detail, evaluable=False)

        missing = [f for f in ("vehicle_id", "start_soc_pct", "origin", "destination") if getattr(request, f) is None]
        if missing:
            return not_evaluable("missing_fields", f"missing {missing}")
        vehicle = self.store.vehicles.get(request.vehicle_id)
        if vehicle is None:  # annotation guide rule 3: an unsupported vehicle is infeasible
            return FeasibilityResult(False, "unknown_vehicle", f"vehicle {request.vehicle_id!r} is not in the data")
        origin, dest = self._resolve(request.origin), self._resolve(request.destination)
        if origin is None or dest is None:
            return not_evaluable("unresolved_place", "origin or destination could not be geocoded")
        try:
            route = self.route_source(origin, dest)
        except Exception as e:  # noqa: BLE001 - any route-source failure makes this one item not evaluable
            # A route source may raise instead of returning None (the OSRM router raises
            # OSRMNoRouteError / OSRMTemporaryError, ValueError for a place without coordinates).
            # Matched by name and LookupError (RouteNotCached) so the oracle needs no planner import.
            name = type(e).__name__
            if isinstance(e, LookupError) or "NoRoute" in name:
                return not_evaluable("no_route", f"no route for {origin.text} -> {dest.text} ({name}: {e})")
            return not_evaluable("route_error", f"{name}: {e}")
        if route is None:
            return not_evaluable("no_route", f"no route for {origin.text} -> {dest.text}")
        poly = [(p.lat, p.lon) for p in route.geometry]
        if len(poly) < 2 or route.distance_km <= 0 or route.duration_min < 0:
            return not_evaluable(
                "degenerate_route",
                f"route {route.route_id} has {len(poly)} point(s), {route.distance_km} km, {route.duration_min} min "
                "(e.g. origin and destination geocode to the same place)",
            )
        length = geo.path_length_km(poly)
        if length <= 0:
            return not_evaluable("degenerate_route", f"route {route.route_id} geometry has zero length")
        scale = route.distance_km / length
        route_key = (route.route_id, route.distance_km, route.duration_min, tuple(poly))
        return _Context(
            request, vehicle, route, poly, scale, float(request.start_soc_pct), self.cfg.floor_dest(request), route_key
        )

    def _road(self, ctx: _Context, located: tuple[float, float]) -> tuple[float, float]:
        along, off = located
        return min(along * ctx.scale, ctx.route.distance_km), off

    def _locate(self, ctx: _Context, station: Station) -> tuple[float, float]:
        """(along road km, offroute straight-line km)."""
        if self.cfg.method == "exhaustive":
            return self._road(ctx, geo.locate((station.lat, station.lon), ctx.poly))
        return self._road(ctx, self._index.locate(self.store.stations, ctx.route_key, ctx.poly, station))

    def has_accessible_restroom(self, station: Station) -> bool:
        """Some amenity in the store is a restroom tagged wheelchair == "yes" within amenity_radius_m
        (straight line) of the station. "unknown" and "limited" never count (proposal section II)."""
        amenities, radius = self.store.amenities, self.cfg.amenity_radius_m
        signature = (id(amenities), len(amenities), radius)
        if signature != self._restroom_signature:
            self._restroom_signature = signature
            self._restroom_ok = {}
            self._accessible_restrooms = [
                (a.lat, a.lon) for a in amenities if a.category == AmenityCategory.RESTROOM and a.wheelchair == "yes"
            ]
        found = self._restroom_ok.get(station.station_id)
        if found is None:
            here = (station.lat, station.lon)
            found = self._restroom_ok[station.station_id] = any(
                geo.great_circle_km(here, a) * 1000.0 <= radius for a in self._accessible_restrooms
            )
        return found

    def _problem(self, request: TripRequest) -> _Problem | FeasibilityResult:
        return self._cached("problem", request, lambda: self._build_problem(request))

    def _build_problem(self, request: TripRequest) -> _Problem | FeasibilityResult:
        ctx = self._context(request)
        if isinstance(ctx, FeasibilityResult):
            return ctx
        leaf = is_charge_time_excluded(ctx.vehicle.vehicle_id)
        if ctx.start < self.cfg.reserve - EPS:
            return FeasibilityResult(
                False,
                "start_below_reserve",
                f"start SoC {ctx.start}% is below the {self.cfg.reserve}% reserve",
                charge_time_excluded=leaf,
            )
        if override_has_no_overlap(ctx.vehicle, request.connectors_override):
            # No station is usable, but a trip that needs no charge is still feasible (and
            # check_itinerary passes a stop-free plan), so only the direct drive is searched.
            # is_feasible reports connector_no_overlap when that direct drive does not reach.
            supported = sorted(c.value for c in ctx.vehicle.supported_connectors())
            wanted = sorted(c.value for c in request.connectors_override or [])
            nodes = [_Node("origin", 0.0), _Node("destination", ctx.route.distance_km)]
            excluded = {"connector": len(self.store.stations)} if self.store.stations else {}
            detail = f"override {wanted} shares no connector with the vehicle's {supported}"
            access = [False, False] if requires_accessible_restroom(request) else None
            return _Problem(
                ctx, nodes, excluded, self.cfg.efficiency_penalty, no_overlap_detail=detail, access_ok=access
            )
        cm, dm = self.cfg.charging_model, self.cfg.detour_model
        max_detour = request.max_detour_min
        excluded: Counter[str] = Counter()
        stations: list[_Node] = []
        corridor = None
        if self.cfg.method == "indexed":  # exact: every station outside it is beyond the buffer
            corridor = self._index.corridor(self.store.stations, ctx.route_key, ctx.poly, self.cfg.corridor_buffer_km)
        for st in self.store.stations.values():
            reason = None
            if st.status in EXCLUDED_STATUSES:
                reason = "status"
            elif st.access != "public":
                reason = "access"
            elif corridor is not None and st.station_id not in corridor:
                reason = "buffer"
            else:
                if corridor is None:
                    along, off = self._locate(ctx, st)
                else:
                    along, off = self._road(ctx, corridor[st.station_id])
                det = detour_minutes(off, dm)
                if off > self.cfg.corridor_buffer_km + EPS:
                    reason = "buffer"
                else:
                    reason = station_rejection(st.ports, ctx.vehicle, request.connectors_override, request.min_power_kw)
                if reason is None and max_detour is not None and det > max_detour + EPS:
                    reason = "detour"  # too far even as the only stop, in either scope
            if reason:
                excluded[reason] += 1
                continue
            ports = usable_ports(
                st.ports, ctx.vehicle, request.connectors_override, request.min_power_kw, cm, self.cfg.unknown_power_kw
            )
            stations.append(_Node("station", along, access_km(off, dm), det, st, ports[0]))
        stations.sort(key=lambda n: (n.along_km, n.station.station_id))
        nodes = [_Node("origin", 0.0), *stations, _Node("destination", ctx.route.distance_km)]
        access = None
        if requires_accessible_restroom(request):
            access = [n.kind == "station" and self.has_accessible_restroom(n.station) for n in nodes]
        return _Problem(ctx, nodes, dict(excluded), self.cfg.efficiency_penalty, access_ok=access)

    # ---------------------------------------------------------------- feasibility (exact)

    def _reach(
        self, p: _Problem, total_limit: float | None, floor_dest: float, access: bool = True
    ) -> tuple[bool, list[int], float]:
        """Can the destination be reached? Depart at the cap from every stop (charging never adds
        detour, so this maximises reach). Labels (soc, cumulative detour) are Pareto-pruned, so under
        a trip-total detour limit a lower-reach, lower-detour path is kept alive.

        With a required accessible restroom (p.access_ok; access=False ignores it) a label also
        records whether a stop so far satisfies it (has). The destination accepts only labels that
        do, and has=1 is better on that component (-has), so such a label can prune one without it.
        Without the requirement the keys are exactly the ones of the search without the rule."""
        n, cap, reserve = len(p.nodes), self.cfg.cap, self.cfg.reserve
        need = p.access_ok if access else None
        labels: list[list[tuple[float, float, int, tuple[int, ...]]]] = [[] for _ in range(n)]
        labels[0] = [(p.ctx.start, 0.0, 0, ())]
        if total_limit is not None:
            key = (lambda lab: (-lab[0], lab[1], -lab[2])) if need else (lambda lab: (-lab[0], lab[1]))
        else:
            key = (lambda lab: (-lab[0], -lab[2])) if need else (lambda lab: (-lab[0],))
        furthest = 0.0
        for i in range(n - 1):
            for soc, det, has, path in labels[i]:
                dep = p.ctx.start if i == 0 else max(soc, cap)
                for j in range(i + 1, n):
                    arr = dep - p.drop(i, j)
                    if j == n - 1:
                        if arr >= floor_dest - EPS and (need is None or has):
                            return True, list(path), p.nodes[-1].along_km
                        continue
                    if arr < reserve - EPS:
                        continue
                    nd = det + p.nodes[j].detour_min
                    if total_limit is not None and nd > total_limit + EPS:
                        continue
                    nh = int(has or (need is not None and need[j]))
                    if _insert(labels[j], (arr, nd, nh, path + (j,)), key):
                        furthest = max(furthest, p.nodes[j].along_km)
        return False, [], furthest

    def is_feasible(self, request: TripRequest) -> FeasibilityResult:
        p = self._problem(request)
        if isinstance(p, FeasibilityResult):
            return copy.deepcopy(p)  # the cached object stays private
        leaf = is_charge_time_excluded(p.ctx.vehicle.vehicle_id)
        total = request.max_detour_min if self.cfg.detour_scope == "total" else None
        ok, path, furthest = self._reach(p, total, p.ctx.floor_dest)
        if ok:
            ids = [p.nodes[k].station.station_id for k in path]
            unknown = [s for s in ids if self.store.stations[s].status == StationStatus.UNKNOWN]
            return FeasibilityResult(
                True,
                None,
                "",
                excluded=dict(p.excluded),
                charge_time_excluded=leaf,
                witness=ids,
                unknown_status_station_ids=unknown,
            )
        if p.access_ok is not None and self._reach(p, total, p.ctx.floor_dest, access=False)[0]:
            reason, detail = (
                "no_accessible_restroom",
                "energy-feasible, but no feasible stop sequence has a charging stop with a restroom tagged "
                f"wheelchair=yes within {self.cfg.amenity_radius_m:g} m (important + wheelchair_required)",
            )
        elif p.no_overlap_detail is not None:
            reason, detail = "connector_no_overlap", p.no_overlap_detail
        elif total is not None and self._reach(p, None, p.ctx.floor_dest, access=False)[0]:
            reason, detail = (
                "detour_total_exceeded",
                f"every feasible stop sequence exceeds {total} min of total detour",
            )
        elif self._reach(p, total, self.cfg.reserve, access=False)[0]:
            reason, detail = (
                "arrival_soc_unreachable",
                f"the destination is reachable but not with {p.ctx.floor_dest}% left",
            )
        elif len(p.nodes) == 2:
            reason, detail = "no_usable_station", f"no usable station; excluded by reason: {p.excluded}"
        else:
            reason, detail = (
                "energy_gap",
                f"furthest reachable point {furthest:.1f} km of {p.ctx.route.distance_km:.1f} km",
            )
        return FeasibilityResult(False, reason, detail, excluded=dict(p.excluded), charge_time_excluded=leaf)

    # ---------------------------------------------------------------- Pareto search

    def _targets(self, p: _Problem, i: int, arrive: float) -> list[float]:
        """Departure SoCs at node i: the exact need of every later node plus a grid, up to the
        need of driving straight to the destination (charging more than that is dominated)."""
        n, cap, step = len(p.nodes), self.cfg.cap, self.cfg.soc_grid_step_pct
        ts: set[float] = set()
        hi = cap
        for j in range(i + 1, n):
            floor = p.ctx.floor_dest if j == n - 1 else self.cfg.reserve
            need = p.drop(i, j) + floor + self.cfg.departure_buffer_pct
            if need <= cap + EPS:
                ts.add(min(need, cap))
                if j == n - 1:
                    hi = min(cap, need)
        k = math.floor(arrive / step) + 1
        while k * step < hi - EPS:
            ts.add(k * step)
            k += 1
        ts.add(hi)
        targets = sorted(t for t in ts if arrive + EPS < t <= hi + EPS)
        if p.access_ok is not None and p.access_ok[i]:
            # A stop for the required accessible restroom need not charge (0 min, charge_minutes):
            # without this, a trip drivable with no charge could never satisfy the requirement.
            targets.insert(0, arrive)
        return targets

    def pareto_plans(self, request: TripRequest) -> ParetoResult:
        """Non-dominated plans. Cached per (request, settings) on the indexed path; always a copy."""
        if self.cfg.method == "exhaustive":
            return self._pareto(request)
        store = self.store
        key = (
            json.dumps(dataclasses.asdict(self.cfg), sort_keys=True, default=str),
            request.model_dump_json(),
            (
                id(store.stations),
                len(store.stations),
                id(store.vehicles),
                len(store.vehicles),
                id(store.amenities),
                len(store.amenities),
            ),
        )
        found = self._fronts.get(key)
        if found is None:
            found = self._fronts[key] = self._pareto_fast(request)
            while len(self._fronts) > MAX_CACHED_FRONTS:
                self._fronts.popitem(last=False)
        else:
            self._fronts.move_to_end(key)
        return copy.deepcopy(found)

    def _pareto(self, request: TripRequest) -> ParetoResult:
        started = time.monotonic()
        budget = self.cfg.pareto_time_budget_s
        p = self._problem(request)
        if isinstance(p, FeasibilityResult):
            return ParetoResult([], True, copy.deepcopy(p))
        ctx, nodes, n = p.ctx, p.nodes, len(p.nodes)
        cm = self.cfg.charging_model
        total = request.max_detour_min if self.cfg.detour_scope == "total" else None
        # Layer h of a node holds the labels with (h=1) or without (h=0) a stop that satisfies the
        # required accessible restroom. Without the requirement there is one layer, and the search is
        # exactly the one without the rule.
        need = p.access_ok
        layers = 1 if need is None else 2
        fronts: list[list[list[_Label]]] = [[[] for _ in range(layers)] for _ in range(n)]
        fronts[0][0] = [_Label(0, 0.0, ctx.start, 0.0, 0.0, None, None)]
        unknown = self._unknown_flags(nodes)
        count, complete, reason = 1, True, None
        for i, h in ((i, h) for i in range(n - 1) for h in range(layers)):
            if not complete:
                break
            for lab in list(fronts[i][h]):
                if budget is not None and time.monotonic() - started >= budget:
                    complete, reason = False, "time_budget"
                    break
                if i == 0:
                    deps = [(ctx.start, 0.0)]
                else:
                    port = nodes[i].port
                    deps = [
                        (t, charge_minutes(lab.soc, t, port.peak_kw, port.level, ctx.vehicle, cm))
                        for t in self._targets(p, i, lab.soc)
                    ]
                for dep, cmin in deps:
                    for j in range(i + 1, n):
                        nh = h or int(need is not None and need[j])
                        if j == n - 1 and nh < layers - 1:
                            continue  # the required accessible restroom was never reached
                        floor = ctx.floor_dest if j == n - 1 else self.cfg.reserve
                        if i > 0:  # leaving a charging stop: the planner's buffer policy, if any
                            floor = floor + self.cfg.departure_buffer_pct
                        arr = dep - p.drop(i, j)
                        if arr < floor - EPS:
                            continue
                        add_det = nodes[j].detour_min if j < n - 1 else 0.0
                        if total is not None and lab.detour + add_det > total + EPS:
                            continue
                        drive = ctx.route.duration_min * (nodes[j].along_km - nodes[i].along_km) / ctx.route.distance_km
                        new = _Label(
                            j,
                            lab.time + cmin + drive + add_det,
                            arr,
                            lab.detour + add_det,
                            lab.charge + cmin,
                            lab,
                            (i, lab.soc, dep, cmin) if i > 0 else None,
                            lab.unk + unknown[j],
                        )
                        k, u = new.key(), new.unk
                        if nh < layers - 1 and any(_beats(o.key(), o.unk, k, u) for o in fronts[j][nh + 1]):
                            continue  # beaten by a label that also has the accessible restroom
                        if _insert_pref(fronts[j][nh], new, _Label.key, _label_unk):
                            count += 1
                            if count > self.cfg.max_labels:
                                complete, reason = False, "max_labels"
                                break
                    if not complete:
                        break
                if not complete:
                    break
        plans = [self._to_plan(p, lab) for lab in fronts[-1][layers - 1]]
        return ParetoResult(_final_front(plans), complete, incomplete_reason=reason, n_labels=count)

    def _pareto_fast(self, request: TripRequest) -> ParetoResult:
        """_pareto (the reference, used by method="exhaustive") with identical results, faster:
        the same labels are created and inserted in the same order with the same float arithmetic;
        dominance is checked through _Front, per-node values are computed once per node, and a
        _Label object is built only for a candidate that is inserted."""
        started = time.monotonic()
        budget = self.cfg.pareto_time_budget_s
        p = self._problem(request)
        if isinstance(p, FeasibilityResult):
            return ParetoResult([], True, copy.deepcopy(p))
        ctx, nodes, n = p.ctx, p.nodes, len(p.nodes)
        cm = self.cfg.charging_model
        total = request.max_detour_min if self.cfg.detour_scope == "total" else None
        reserve, floor_dest, max_labels = self.cfg.reserve, ctx.floor_dest, self.cfg.max_labels
        buffer = self.cfg.departure_buffer_pct
        duration, distance = ctx.route.duration_min, ctx.route.distance_km
        need = p.access_ok  # same layers as _pareto (one layer without the requirement)
        layers = 1 if need is None else 2
        top = layers - 1
        fronts = [[_Front() for _ in range(layers)] for _ in range(n)]
        first = _Label(0, 0.0, ctx.start, 0.0, 0.0, None, None)
        fronts[0][0].add(first, first.key())
        unknown = self._unknown_flags(nodes)
        count, complete, reason = 1, True, None
        last = n - 1
        for i, h in ((i, h) for i in range(n - 1) for h in range(layers)):
            if not complete:
                break
            # Per node i: the reference recomputes these for every label and departure, identically.
            succ = []  # (j, drop, floor - EPS, add_det, drive, unknown_j, layer of the new label)
            for j in range(i + 1, n):
                nh = h or int(need is not None and need[j])
                if j == last and nh < top:
                    continue  # the required accessible restroom was never reached
                floor = floor_dest if j == last else reserve
                if i > 0:  # same buffer policy as _pareto
                    floor = floor + buffer
                add_det = nodes[j].detour_min if j < last else 0.0
                drive = duration * (nodes[j].along_km - nodes[i].along_km) / distance
                succ.append((j, p.drop(i, j), floor - EPS, add_det, drive, unknown[j], nh))
            port = nodes[i].port
            for lab in list(fronts[i][h].labels):
                if budget is not None and time.monotonic() - started >= budget:
                    complete, reason = False, "time_budget"
                    break
                if i == 0:
                    deps = [(ctx.start, 0.0)]
                else:
                    deps = [
                        (t, charge_minutes(lab.soc, t, port.peak_kw, port.level, ctx.vehicle, cm))
                        for t in self._targets(p, i, lab.soc)
                    ]
                lab_time, lab_det, lab_charge, lab_unk = lab.time, lab.detour, lab.charge, lab.unk
                for dep, cmin in deps:
                    t_base = lab_time + cmin  # the reference's lab.time + cmin + drive + add_det, left to right
                    charge = lab_charge + cmin
                    t3 = charge + EPS
                    stop = (i, lab.soc, dep, cmin) if i > 0 else None
                    for j, drop, floor_eps, add_det, drive, unk_j, nh in succ:
                        arr = dep - drop
                        if arr < floor_eps:
                            continue
                        detour = lab_det + add_det
                        if total is not None and detour > total + EPS:
                            continue
                        t = t_base + drive + add_det
                        neg_soc = -arr
                        front = fronts[j][nh]
                        unk = lab_unk + unk_j
                        key = (t, neg_soc, detour, charge)
                        if nh < top and fronts[j][top].dominated(t + EPS, neg_soc + EPS, detour + EPS, t3, key, unk):
                            continue  # beaten by a label that also has the accessible restroom
                        if front.dominated(t + EPS, neg_soc + EPS, detour + EPS, t3, key, unk):
                            continue
                        new = _Label(j, t, arr, detour, charge, lab, stop, unk)
                        front.add(new, key)
                        count += 1
                        if count > max_labels:
                            complete, reason = False, "max_labels"
                            break
                    if not complete:
                        break
                if not complete:
                    break
        plans = [self._to_plan(p, lab) for lab in fronts[-1][top].labels]
        return ParetoResult(_final_front(plans), complete, incomplete_reason=reason, n_labels=count)

    def _unknown_flags(self, nodes: list[_Node]) -> list[int]:
        """1 for a station node whose status is UNKNOWN (the tie-break of _beats), else 0."""
        return [int(n.kind == "station" and n.station.status == StationStatus.UNKNOWN) for n in nodes]

    def _to_plan(self, p: _Problem, lab: _Label) -> OraclePlan:
        stops: list[OracleStop] = []
        cur = lab
        while cur is not None:
            if cur.stop is not None:
                i, arrive, depart, cmin = cur.stop
                node = p.nodes[i]
                stops.append(
                    OracleStop(
                        node.station.station_id,
                        node.port.connector.value,
                        node.port.power_kw,
                        node.port.estimated,
                        node.along_km,
                        node.detour_min,
                        arrive,
                        depart,
                        cmin,
                    )
                )
            cur = cur.parent
        stops.reverse()
        unknown = tuple(
            s.station_id for s in stops if self.store.stations[s.station_id].status == StationStatus.UNKNOWN
        )
        return OraclePlan(
            tuple(stops), p.ctx.route.duration_min + lab.detour + lab.charge, lab.detour, lab.charge, lab.soc, unknown
        )

    # ---------------------------------------------------------------- itinerary check (independent replay)

    def check_itinerary(self, request: TripRequest, itinerary: Itinerary) -> CheckReport:
        """Replay an itinerary under the documented model and list every violated hard constraint.

        Distances come from the oracle's own geometry, never from the itinerary's legs.
        Raises OracleUnavailable when the request cannot be evaluated.

        `ok` uses the tolerances soc_tol_pct / detour_tol_min (planner rounding, OSRM detours).
        `strict_ok` repeats the checks with the search's own EPS: a plan that passes only within
        tolerance can lie outside the oracle front or pass on an oracle-infeasible request, and the
        findings use strict_ok so that gap is not reported as a disagreement.
        """
        report = self._check(request, itinerary, self.cfg.soc_tol_pct, self.cfg.detour_tol_min)
        if not report.ok:
            report.strict_ok = False
            return report
        strict = self._check(request, itinerary, EPS, EPS)
        report.strict_ok = strict.ok
        report.strict_violations = sorted({v.code for v in strict.violations})
        return report

    def _check(self, request: TripRequest, itinerary: Itinerary, soc_tol: float, detour_tol: float) -> CheckReport:
        """check_itinerary with explicit SoC / detour tolerances."""
        ctx = self._context(request)
        if isinstance(ctx, FeasibilityResult):
            raise OracleUnavailable(f"{ctx.reason}: {ctx.detail}")
        cfg, cm, dm = self.cfg, self.cfg.charging_model, self.cfg.detour_model
        vehicle, override, max_detour = ctx.vehicle, request.connectors_override, request.max_detour_min
        native, adapters = allowed_connectors(vehicle, override)
        allowed_txt = f"native {sorted(c.value for c in native)}, DC-fast adapters {sorted(c.value for c in adapters)}"
        violations: list[Violation] = []
        notes: list[str] = []

        def bad(code: str, detail: str, k: int | None = None) -> None:
            violations.append(Violation(code=code, detail=detail, stop_index=k))

        soc, prev_along, prev_access = ctx.start, 0.0, 0.0
        replay, priced = True, True
        restroom_required = requires_accessible_restroom(request)
        restroom_found = False
        oracle_km = detour_sum = charge_sum = 0.0
        uses_unknown = False
        for k, stop in enumerate(itinerary.stops):
            st = self.store.stations.get(stop.station_id)
            if st is None:
                bad("unknown_station", f"station {stop.station_id!r} is not in the data", k)
                replay = False
                continue
            if st.status in EXCLUDED_STATUSES:
                bad("status", f"{st.station_id} is {st.status.value}", k)
            if st.access != "public":
                bad("access", f"{st.station_id} is {st.access}", k)
            if restroom_required and not restroom_found:
                restroom_found = self.has_accessible_restroom(st)
            same = [p for p in st.ports if p.connector == stop.connector]
            compat = [p for p in same if port_compatible(p, vehicle, override)]
            if not compat:
                bad(
                    "connector",
                    f"{stop.connector.value} at {st.station_id} is not usable ({allowed_txt}; "
                    f"station has {[(p.connector.value, p.level.value) for p in st.ports]})",
                    k,
                )
            ports = compat or same
            # Power rules apply to the port the stop uses (identified by its claimed power), not to
            # any port of that connector: CCS1 50 + 150 kW with a stop on the 50 kW port fails
            # min_power_kw=100. A claimed power no port has is its own violation below; the rules
            # then fall back to every port of the connector.
            used = [p for p in ports if _same_power(p.power_kw, stop.port_power_kw)] or ports
            if request.min_power_kw is not None and not any(meets_min_power(p, request.min_power_kw) for p in used):
                bad(
                    "power",
                    f"the {stop.connector.value} port used at {st.station_id} ({stop.port_power_kw} kW) "
                    f"is not >= {request.min_power_kw} kW",
                    k,
                )
            if used and all(p.power_kw is not None and p.power_kw <= 0 for p in used):
                bad("power", f"{st.station_id} {stop.connector.value} reports non-positive power", k)
            if ports and not any(_same_power(p.power_kw, stop.port_power_kw) for p in ports):
                bad(
                    "power",
                    f"claimed {stop.port_power_kw} kW is not a {stop.connector.value} port power at "
                    f"{st.station_id} ({[p.power_kw for p in ports]})",
                    k,
                )

            along, off = self._locate(ctx, st)
            acc, det = access_km(off, dm), detour_minutes(off, dm)
            detour_sum += det
            if along < prev_along - cfg.continuity_tol_km:
                bad("continuity", f"stop at {along:.1f} km comes after one at {prev_along:.1f} km", k)
            if (
                cfg.detour_scope == "stop"
                and max_detour is not None
                and (det > max_detour + detour_tol or stop.detour_min > max_detour + EPS)
            ):
                bad("detour", f"detour {max(det, stop.detour_min):.1f} min > limit {max_detour} min", k)
            if abs(stop.detour_min - det) > detour_tol:
                notes.append(f"stop {k}: claimed detour {stop.detour_min:.1f} min, oracle {det:.1f} min")
            if stop.depart_soc_pct < stop.arrive_soc_pct - soc_tol:
                bad(
                    "departure_soc",
                    f"departs with {stop.depart_soc_pct}% after arriving with {stop.arrive_soc_pct}%",
                    k,
                )
            if stop.depart_soc_pct > cfg.cap + soc_tol:
                bad("departure_soc", f"departs with {stop.depart_soc_pct}% > cap {cfg.cap}%", k)

            if replay:
                km = prev_access + max(0.0, along - prev_along) + acc
                oracle_km += km
                arrive = soc - leg_soc_drop_pct(km, vehicle, cfg.efficiency_penalty)
                if arrive < cfg.reserve - soc_tol:
                    bad("reserve", f"arrives at {st.station_id} with {arrive:.1f}% < reserve {cfg.reserve}%", k)
                if abs(arrive - stop.arrive_soc_pct) > soc_tol:
                    notes.append(f"stop {k}: claimed arrival {stop.arrive_soc_pct:.1f}%, oracle {arrive:.1f}%")
                depart = min(stop.depart_soc_pct, cfg.cap)
                price = _pricing_port(ports, stop.port_power_kw)
                if price is None:
                    priced = False
                else:
                    kw, est = port_power_kw(price, cm, cfg.unknown_power_kw)
                    uses_unknown |= est
                    charge_sum += charge_minutes(
                        arrive, depart, peak_kw(kw, price.level, vehicle, cm), price.level, vehicle, cm
                    )
                soc = max(arrive, depart)  # a car that arrives fuller than planned leaves without charging
            prev_along, prev_access = along, acc

        if replay:
            km = prev_access + max(0.0, ctx.route.distance_km - prev_along)
            oracle_km += km
            arrival = soc - leg_soc_drop_pct(km, vehicle, cfg.efficiency_penalty)
            if arrival < cfg.reserve - soc_tol:
                bad("reserve", f"arrives at the destination with {arrival:.1f}% < reserve {cfg.reserve}%")
            if arrival < ctx.floor_dest - soc_tol:
                bad("arrival_soc", f"arrives with {arrival:.1f}% < required {ctx.floor_dest}%")
            if itinerary.legs:
                legs_km = sum(leg.distance_km for leg in itinerary.legs)
                if abs(legs_km - oracle_km) > cfg.route_distance_tol * oracle_km:
                    bad("route_distance", f"legs sum to {legs_km:.1f} km, oracle {oracle_km:.1f} km")
        if restroom_required and not restroom_found:
            # Contract code `access` (as the planner's verifier, #150); a code of its own would be a
            # contract change (ADR 0004). The detail names the rule.
            bad(
                "access",
                "required wheelchair-accessible restroom missing: no charging stop has a restroom tagged "
                f"wheelchair=yes within {cfg.amenity_radius_m:g} m",
            )
        if (
            cfg.detour_scope == "total"
            and max_detour is not None
            and (detour_sum > max_detour + detour_tol or itinerary.total_detour_min > max_detour + EPS)
        ):
            bad(
                "detour", f"total detour {max(detour_sum, itinerary.total_detour_min):.1f} min > limit {max_detour} min"
            )
        objectives = (
            (ctx.route.duration_min + detour_sum + charge_sum, detour_sum, charge_sum) if replay and priced else None
        )
        return CheckReport(not violations, violations, notes, objectives, uses_unknown)

    def is_valid(self, itinerary: Itinerary, request: TripRequest) -> bool:
        return self.check_itinerary(request, itinerary).ok

    def check_plan_result(self, request: TripRequest, result: PlanResult) -> dict[str, list[CheckReport]]:
        """Check every returned alternative, and separately every relaxation alternative."""
        relaxed = result.relaxation.alternatives if result.relaxation else []
        return {
            "alternatives": [self.check_itinerary(request, a.itinerary) for a in result.alternatives],
            "relaxed": [self.check_itinerary(request, a.itinerary) for a in relaxed],
        }


def _same_power(a: float | None, b: float | None) -> bool:
    return (a is None and b is None) or (a is not None and b is not None and abs(a - b) < 1e-6)


def _pricing_port(ports, claimed_kw: float | None):
    """Port used to price a replayed stop: the one matching the claimed power, else the fastest usable."""
    usable = [p for p in ports if p.power_kw is None or p.power_kw > 0]
    match = [p for p in usable if _same_power(p.power_kw, claimed_kw)]
    pool = match or usable
    if not pool:
        return None
    return max(pool, key=lambda p: -1.0 if p.power_kw is None else p.power_kw)


def dominated_by_front(point: tuple[float, float, float], plans: list[OraclePlan], tol: float) -> bool:
    """True when some oracle plan is at least as good as `point` on every objective (within tol)."""
    return any(_weakly_dominates(plan.objectives, point, tol) for plan in plans)


def strictly_dominating(point: tuple[float, float, float], plans: list[OraclePlan], tol: float) -> list[OraclePlan]:
    """Oracle plans no worse than `point` on every objective and better by more than tol on one."""

    def beats(objectives) -> bool:
        pairs = list(zip(objectives, point, strict=True))
        return all(a <= b + tol for a, b in pairs) and any(a < b - tol for a, b in pairs)

    return [p for p in plans if beats(p.objectives)]
