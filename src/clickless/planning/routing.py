"""Cached fixture routing and cache-first runtime OSRM routing.

CachedRouter keeps Configuration A deterministic using committed fixtures and
a geometric detour estimate. OSRMRouter strictly fetches and validates live
road routes. CachingOSRMRouter checks OSRMRouteCache before using the live
client. Route-measurement policy separately decides whether an explicitly
temporary failure may use a labelled geometric estimate.

Owner: Hriday.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Protocol

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
from .route_cache import OSRMRouteCache


class RouteNotCached(LookupError):
    pass


class OSRMRouteError(RuntimeError):
    """Base class for failures produced while requesting an OSRM route."""


class OSRMTemporaryError(OSRMRouteError):
    """A temporary OSRM failure for which geometric fallback is permitted."""


class OSRMNoRouteError(OSRMRouteError):
    """OSRM successfully responded but found no driveable route."""


class OSRMResponseError(OSRMRouteError):
    """OSRM returned an HTTP or response payload that cannot be used safely."""


def is_geometric_fallback_allowed(error: Exception) -> bool:
    """Return whether routing policy may use the geometric estimate.

    Only explicitly temporary service failures are eligible. Invalid input,
    configuration errors, NoRoute results and malformed responses must remain
    visible instead of being disguised as estimated road measurements.
    """

    return isinstance(error, OSRMTemporaryError)


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
        path = (
            self.store.routes_dir
            / f"{_slug(origin.text)}__{_slug(destination.text)}.json"
        )
        if not path.exists():
            raise RouteNotCached(f"no cached route at {path}")
        payload = json.loads(path.read_text())
        payload.pop("_meta", None)
        return Route.model_validate(payload)

    def detour_min(self, route: Route, candidate: Candidate) -> float:
        """PLACEHOLDER: out-and-back along a straight line at low speed.

        Keep this deterministic estimate for Configuration A and as the
        fallback measurement. OSRMRouter performs the real waypoint reroute;
        the measurement-policy layer chooses and labels the source used.
        """
        c = self.detour_cfg
        road_km = 2 * candidate.offroute_km * c["road_factor"]
        return c["fixed_min"] + 60.0 * road_km / c["offroute_speed_kmh"]


class OSRMRouter:
    """Fetch and validate routes from an OSRM HTTP service.

    Caching is deliberately handled by CachingOSRMRouter so this client remains
    responsible only for live HTTP requests and OSRM response validation. Tests
    inject a mock HTTP session so the normal suite remains offline.
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

        return self.route_through(
            [
                origin_coordinates,
                destination_coordinates,
            ]
        )

    def route_through(
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
            raise OSRMTemporaryError(
                "OSRM request timed out after " f"{self.timeout_seconds:.1f} seconds"
            ) from error
        except requests.ConnectionError as error:
            raise OSRMTemporaryError(f"OSRM connection failed: {error}") from error
        except requests.HTTPError as error:
            status_code = getattr(
                error.response,
                "status_code",
                None,
            )

            if status_code is not None and 500 <= status_code < 600:
                raise OSRMTemporaryError(
                    f"OSRM temporarily failed with HTTP {status_code}"
                ) from error

            status_description = (
                str(status_code) if status_code is not None else "unknown"
            )
            raise OSRMResponseError(
                "OSRM request failed with non-retryable HTTP " f"{status_description}"
            ) from error
        except requests.RequestException as error:
            raise OSRMRouteError(f"OSRM request failed: {error}") from error

        try:
            response_payload = response.json()
        except ValueError as error:
            raise OSRMResponseError(
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
            failure_message = (
                f"OSRM route failed with {error_code}: " f"{error_message}"
            )

            if error_code == "NoRoute":
                raise OSRMNoRouteError(failure_message)

            raise OSRMResponseError(failure_message)

        returned_routes = response_payload.get("routes", [])

        if not returned_routes:
            raise OSRMResponseError("OSRM returned success but included no routes")

        selected_route = returned_routes[0]
        geometry_payload = selected_route.get("geometry", {})
        geometry_coordinates = geometry_payload.get(
            "coordinates",
            [],
        )

        if len(geometry_coordinates) < 2:
            raise OSRMResponseError(
                "OSRM route geometry must contain at least two points"
            )

        try:
            # OSRM reports distance in metres and duration in seconds.
            # ClickLess contracts use kilometres and minutes.
            distance_km = float(selected_route["distance"]) / 1000.0
            duration_min = float(selected_route["duration"]) / 60.0
        except (KeyError, TypeError, ValueError) as error:
            raise OSRMResponseError(
                "OSRM route must contain numeric distance and duration"
            ) from error

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
            distance_km=distance_km,
            duration_min=duration_min,
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

        route_via_station = self.route_through(
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


class CachingOSRMRouter:
    """Reuse cached OSRM measurements before requesting live measurements.

    This wrapper coordinates storage and live routing without weakening the
    strict behavior of OSRMRouter. A cache miss calls OSRM; an OSRM failure is
    still raised to the caller and does not silently select geometric fallback.
    """

    def __init__(
        self,
        live_router: OSRMRouter,
        route_cache: OSRMRouteCache,
    ) -> None:
        self.live_router = live_router
        self.route_cache = route_cache

    def route_through(
        self,
        route_points: Sequence[LatLon],
    ) -> Route:
        """Return a cached waypoint route or fetch and save a live one.

        Keeping this operation public lets other routing consumers, including
        independent verification, request the same cache-first measurement
        without depending on this wrapper's private implementation details.
        """

        cached_route = self.route_cache.load(route_points)

        if cached_route is not None:
            return cached_route

        # OSRMRouter remains the only component responsible for HTTP requests
        # and validating the response returned by the OSRM service.
        live_route = self.live_router.route_through(route_points)
        self.route_cache.save(
            route_points,
            live_route,
        )

        return live_route

    def route(
        self,
        origin: Place,
        destination: Place,
    ) -> Route:
        """Return the direct route, preferring a cached OSRM measurement."""

        origin_coordinates = _coordinates_for_place(origin)
        destination_coordinates = _coordinates_for_place(destination)

        return self.route_through(
            [
                origin_coordinates,
                destination_coordinates,
            ]
        )

    def detour_min(
        self,
        route: Route,
        candidate: Candidate,
    ) -> float:
        """Return added time for a cached or live route through one station."""

        station_coordinates = LatLon(
            lat=candidate.station.lat,
            lon=candidate.station.lon,
        )

        route_via_station = self.route_through(
            [
                route.origin,
                station_coordinates,
                route.destination,
            ]
        )

        measured_detour_min = route_via_station.duration_min - route.duration_min

        return max(
            0.0,
            measured_detour_min,
        )


class CandidateDetourMeasurer(Protocol):
    """Source of one candidate station's added driving time."""

    def detour_min(
        self,
        route: Route,
        candidate: Candidate,
    ) -> float: ...


class TemporaryFailureDetourMeasurer:
    """Use geometric candidate detours only for temporary OSRM failures.

    One planning attempt can measure several candidate stations. Once any
    candidate uses fallback, the warning remains active until ``reset`` starts
    the next attempt, even if later candidate measurements succeed through
    OSRM.
    """

    def __init__(
        self,
        primary_measurer: CandidateDetourMeasurer,
        fallback_measurer: CandidateDetourMeasurer,
    ) -> None:
        self.primary_measurer = primary_measurer
        self.fallback_measurer = fallback_measurer
        self.fallback_used = False
        self.warning: str | None = None

    def reset(self) -> None:
        """Clear fallback state before measuring a new candidate collection."""

        self.fallback_used = False
        self.warning = None

    def detour_min(
        self,
        route: Route,
        candidate: Candidate,
    ) -> float:
        """Measure one detour, falling back only after a temporary failure."""

        try:
            return self.primary_measurer.detour_min(
                route,
                candidate,
            )
        except OSRMTemporaryError as error:
            fallback_detour_min = self.fallback_measurer.detour_min(
                route,
                candidate,
            )
            self.fallback_used = True
            self.warning = (
                f"OSRM candidate-detour measurement unavailable ({error}); "
                "using a deterministic geometric estimate."
            )
            return fallback_detour_min
