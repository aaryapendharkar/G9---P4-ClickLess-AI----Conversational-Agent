"""Route contract. Producer/owner: Hriday (OSRM client + route cache)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from .provenance import Provenance


class LatLon(BaseModel):
    lat: float
    lon: float


class Route(BaseModel):
    route_id: str
    origin: LatLon
    destination: LatLon
    via: list[LatLon] = Field(default_factory=list)
    geometry: list[LatLon]
    distance_km: float  # road distance from OSRM (not straight-line)
    duration_min: float
    provenance: Provenance
