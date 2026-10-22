"""Candidate contract: a corridor station enriched with nearby amenities (and KG evidence in C).

Producer/owner: Manas (corridor retrieval, matching, KG enrichment). Consumers: planning, ranking.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .data import Amenity, Station
from .provenance import Evidence


class NearbyAmenity(BaseModel):
    amenity: Amenity
    distance_m: float
    # preference key (AmenityPreference.key) -> match score in [0, 1] from TF-IDF / SBERT
    match_scores: dict[str, float] = Field(default_factory=dict)


class Candidate(BaseModel):
    station: Station
    route_index: int  # index of the nearest route-geometry segment
    along_route_km: float  # straight-line distance along the route geometry to that point
    offroute_km: float  # straight-line distance from the route to the station
    detour_min_est: float | None = None  # filled by planning.routing (reroute through station)
    amenities: list[NearbyAmenity] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)  # filled by KG enrichment (Config C)
