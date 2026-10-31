"""Routing: cached fixture routes and runtime OSRM routes. Owner: Hriday.

CachedRouter keeps Configuration A deterministic by using committed route
fixtures and a geometric detour estimate. OSRMRouter is a strict HTTP client:
it returns OSRM measurements or raises OSRMRouteError. Caching and explicit
geometric fallback policy are separate follow-up milestones.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

import requests

from ..contracts import (
    Candidate,
    DataStatus,
    LatLon,
    Place,
    Provenance,
    Route,
)
from ..data import DataStore


class RouteNotCached(LookupError):
    pass


class OSRMRouteError(RuntimeError):
    """Raised when OSRM cannot return a valid route."""


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")


def _coordinates_for_place(place: Place) -> LatLon:
    """Convert a parsed place into validated routing coordinates.

    Natural-language parsing can produce a place name before geocoding has
    supplied latitude and longitude. OSRM cannot route a text-only place.
    """

    if place.lat is None or place.lon is None:
        raise ValueError(f"{place.text!r} must have latitude and longitude for routing")

    return LatLon(
        lat=place.lat,
        lon=place.lon,
    )


def _format_osrm_coordinate(coordinates: LatLon) -> str:
    """Return one coordinate in OSRM's longitude,latitude order."""

    return f"{coordinates.lon:.6f},{coordinates.lat:.6f}"


def _build_route_identifier(coordinate_text: str) -> str:
    """Build a short, stable identifier without exposing raw coordinates."""

    coordinate_digest = sha256(coordinate_text.encode("utf-8")).hexdigest()[:16]

    return f"osrm-{coordinate_digest}"


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
    """Fetch routes from an OSRM HTTP service.

    Runtime response caching is intentionally handled in a later milestone.
    Tests inject a mock HTTP session so the normal suite remains offline.
    """

    def __init__(
        self,
        base_url: str,
        http_session: requests.Session | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        if not base_url.strip():
            raise ValueError("OSRM base URL must not be empty")

        self.base_url = base_url.rstrip("/")
        self.http_session = http_session or requests.Session()
        self.timeout_seconds = timeout_seconds

        # The public OSRM demo service asks clients to identify themselves.
        # This value also makes project requests recognizable during debugging.
        self.http_session.headers.update(
            {"User-Agent": ("ClickLess-Charge/0.1 " "(ASU CSE 573 course project)")}
        )

    def route(
        self,
        origin: Place,
        destination: Place,
    ) -> Route:
        """Fetch one direct origin-to-destination driving route."""

        origin_coordinates = _coordinates_for_place(origin)
        destination_coordinates = _coordinates_for_place(destination)

        return self._fetch_route_through(
            [
                origin_coordinates,
                destination_coordinates,
            ]
        )

    def _fetch_route_through(
        self,
        route_points: Sequence[LatLon],
    ) -> Route:
        """Fetch a route through every supplied point in the given order.

        Two points create a direct route. Additional middle points become OSRM
        waypoints, such as charging stations that the route must visit.
        """

        if len(route_points) < 2:
            raise ValueError("OSRM routing requires at least an origin and destination")

        coordinate_text = ";".join(
            _format_osrm_coordinate(point) for point in route_points
        )

        request_url = f"{self.base_url}/route/v1/driving/{coordinate_text}"
        request_options = {
            "overview": "full",
            "geometries": "geojson",
            "steps": "false",
        }

        try:
            response = self.http_session.get(
                request_url,
                params=request_options,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
        except requests.Timeout as error:
            raise OSRMRouteError(
                f"OSRM request timed out after "
                f"{self.timeout_seconds:.1f} seconds"
            ) from error
        except requests.RequestException as error:
            raise OSRMRouteError(f"OSRM request failed: {error}") from error

        try:
            response_payload = response.json()
        except ValueError as error:
            raise OSRMRouteError(
                "OSRM returned a response that was not valid JSON"
            ) from error

        if response_payload.get("code") != "Ok":
            error_code = response_payload.get(
                "code",
                "unknown",
            )
            error_message = response_payload.get(
                "message",
                "OSRM did not provide an error message",
            )

            raise OSRMRouteError(
                f"OSRM route failed with {error_code}: "
                f"{error_message}"
            )

        returned_routes = response_payload.get("routes", [])

        if not returned_routes:
            raise OSRMRouteError("OSRM returned success but included no routes")

        selected_route = returned_routes[0]
        geometry_payload = selected_route.get("geometry", {})
        geometry_coordinates = geometry_payload.get(
            "coordinates",
            [],
        )

        if len(geometry_coordinates) < 2:
            raise OSRMRouteError("OSRM route geometry must contain at least two points")

        # GeoJSON coordinates are [longitude, latitude]. Our LatLon contract
        # stores explicitly named latitude and longitude values.
        route_geometry = [
            LatLon(
                lat=latitude,
                lon=longitude,
            )
            for longitude, latitude in geometry_coordinates
        ]

        origin_coordinates = route_points[0]
        destination_coordinates = route_points[-1]
        waypoint_coordinates = list(route_points[1:-1])

        return Route(
            route_id=_build_route_identifier(coordinate_text),
            origin=origin_coordinates,
            destination=destination_coordinates,
            via=waypoint_coordinates,
            geometry=route_geometry,
            # OSRM reports meters and seconds. Project contracts use kilometres
            # and minutes so downstream calculations use consistent units.
            distance_km=selected_route["distance"] / 1000.0,
            duration_min=selected_route["duration"] / 60.0,
            provenance=Provenance(
                source="OSRM",
                source_url=response.url,
                retrieved_at=datetime.now(timezone.utc),
                status=DataStatus.RUNTIME,
            ),
        )

    def detour_min(
        self,
        route: Route,
        candidate: Candidate,
    ) -> float:
        """Return added driving time for routing through one station.

        The base route already represents origin to destination. We request a
        second route that must pass through the candidate station, then compare
        the two complete route durations.
        """

        station_coordinates = LatLon(
            lat=candidate.station.lat,
            lon=candidate.station.lon,
        )

        route_via_station = self._fetch_route_through(
            [
                route.origin,
                station_coordinates,
                route.destination,
            ]
        )

        measured_detour_min = route_via_station.duration_min - route.duration_min

        # Routing updates or floating-point conversions could make two nearly
        # equal durations differ by a tiny negative value. A detour cannot
        # logically remove time, so keep the public result non-negative.
        return max(0.0, measured_detour_min)
