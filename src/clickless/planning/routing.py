"""Routing: cached routes (fixtures / frozen tests) and runtime OSRM. Owner: Hriday.

Detours: the proposal measures detour by re-routing origin -> station -> destination. Until that
is wired in, CachedRouter.detour_min uses a crude straight-line placeholder.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..contracts import Candidate, Place, Route
from ..data import DataStore


class RouteNotCached(LookupError):
    pass


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")


class CachedRouter:
    def __init__(self, store: DataStore, energy_cfg: dict[str, Any]) -> None:
        self.store = store
        self.detour_cfg = energy_cfg["placeholder_detour"]

    def route(self, origin: Place, destination: Place) -> Route:
        path = self.store.routes_dir / f"{_slug(origin.text)}__{_slug(destination.text)}.json"
        if not path.exists():
            raise RouteNotCached(f"no cached route at {path}")
        payload = json.loads(path.read_text())
        payload.pop("_meta", None)
        return Route.model_validate(payload)

    def detour_min(self, route: Route, candidate: Candidate) -> float:
        """PLACEHOLDER: out-and-back along a straight line at low speed.

        TODO(Hriday): OSRM reroute through the station; detour = rerouted duration - base duration.
        """
        c = self.detour_cfg
        road_km = 2 * candidate.offroute_km * c["road_factor"]
        return c["fixed_min"] + 60.0 * road_km / c["offroute_speed_kmh"]


class OSRMRouter:
    """Runtime OSRM client (demo) that writes every response into the route cache (tests)."""

    def __init__(self, base_url: str, cache_dir: str = "data/cache/osrm") -> None:
        self.base_url = base_url.rstrip("/")
        self.cache_dir = cache_dir

    def route(self, origin: Place, destination: Place) -> Route:
        # GET {base}/route/v1/driving/{lon},{lat};{lon},{lat}?overview=full&geometries=geojson
        raise NotImplementedError("TODO(Hriday): OSRM route + cache + Provenance(status=RUNTIME)")

    def detour_min(self, route: Route, candidate: Candidate) -> float:
        raise NotImplementedError("TODO(Hriday): reroute via station and subtract base duration")
