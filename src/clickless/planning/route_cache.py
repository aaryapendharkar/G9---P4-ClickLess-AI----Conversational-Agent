"""Stable cache keys and local storage for OSRM route measurements.

OSRMRouteCache stores validated Route objects using deterministic request keys.
It does not contact OSRM or decide when network or geometric fallback is used.
Owner: Hriday.
"""

import json
from collections.abc import Sequence
from hashlib import sha256
from pathlib import Path

from pydantic import ValidationError

from ..contracts import DataStatus, LatLon, Route

# Increment this number if the meaning or structure of stored cache entries
# changes. Old entries must not accidentally be interpreted using a new format.
OSRM_CACHE_FORMAT_VERSION = 1


def build_osrm_cache_key(route_points: Sequence[LatLon]) -> str:
    """Return a stable key for one ordered OSRM route request.

    Two route points represent a direct route. Any points between the first and
    last positions are required waypoints, such as charging stations.

    Coordinates use the same six-decimal precision as OSRMRouter's request URL.
    This means two requests that are identical from OSRM's perspective also
    share one cache entry.
    """

    if len(route_points) < 2:
        raise ValueError(
            "an OSRM cache key requires at least an origin and destination"
        )

    ordered_coordinates = [
        {
            "latitude": f"{point.lat:.6f}",
            "longitude": f"{point.lon:.6f}",
        }
        for point in route_points
    ]

    # Include routing options as well as coordinates. If a future implementation
    # changes the profile or response geometry, it must not reuse incompatible
    # entries created for the current request format.
    cache_identity = {
        "format_version": OSRM_CACHE_FORMAT_VERSION,
        "profile": "driving",
        "request_options": {
            "geometries": "geojson",
            "overview": "full",
            "steps": "false",
        },
        "ordered_coordinates": ordered_coordinates,
    }

    # Sorting object keys makes serialization deterministic. The coordinate
    # list remains ordered because list order represents the driving sequence.
    canonical_identity = json.dumps(
        cache_identity,
        sort_keys=True,
        separators=(",", ":"),
    )

    return sha256(canonical_identity.encode("utf-8")).hexdigest()


class OSRMRouteCacheError(RuntimeError):
    """Raised when an existing OSRM cache entry cannot be read safely."""


class OSRMRouteCache:
    """Store and retrieve validated OSRM routes on the local filesystem.

    A missing cache entry is normal and returns None. An entry that exists but
    cannot be decoded is an error because silently ignoring corruption would
    make test and production behavior difficult to diagnose.
    """

    def __init__(self, cache_directory: Path) -> None:
        self.cache_directory = cache_directory

    def _entry_path(self, route_points: Sequence[LatLon]) -> Path:
        """Return the JSON path assigned to one ordered route request."""

        cache_key = build_osrm_cache_key(route_points)

        return self.cache_directory / f"{cache_key}.json"

    def load(
        self,
        route_points: Sequence[LatLon],
    ) -> Route | None:
        """Load a cached route, or return None when no entry exists."""

        cache_entry_path = self._entry_path(route_points)

        if not cache_entry_path.exists():
            return None

        try:
            cached_route = Route.model_validate_json(
                cache_entry_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError, ValueError) as error:
            raise OSRMRouteCacheError(
                f"could not read OSRM cache entry {cache_entry_path.name}"
            ) from error

        # The stored route records when OSRM originally retrieved the result.
        # When reused from disk, its current delivery status is CACHED.
        cached_provenance = cached_route.provenance.model_copy(
            update={"status": DataStatus.CACHED}
        )

        return cached_route.model_copy(update={"provenance": cached_provenance})

    def save(
        self,
        route_points: Sequence[LatLon],
        route: Route,
    ) -> Path:
        """Save a validated OSRM route and return its cache-file path."""

        self.cache_directory.mkdir(
            parents=True,
            exist_ok=True,
        )

        cache_entry_path = self._entry_path(route_points)
        temporary_entry_path = cache_entry_path.with_suffix(".json.tmp")

        try:
            # Write to a temporary file first. Replacing the final path only
            # after writing succeeds prevents half-written cache entries.
            temporary_entry_path.write_text(
                route.model_dump_json(indent=2),
                encoding="utf-8",
            )
            temporary_entry_path.replace(cache_entry_path)
        except OSError as error:
            # A failed write may leave the temporary file behind. Removing it
            # keeps later cache inspections from confusing it with valid data.
            temporary_entry_path.unlink(missing_ok=True)

            raise OSRMRouteCacheError(
                f"could not write OSRM cache entry {cache_entry_path.name}"
            ) from error

        return cache_entry_path
