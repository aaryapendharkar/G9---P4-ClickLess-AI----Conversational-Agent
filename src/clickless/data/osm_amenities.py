"""OpenStreetMap amenities via Overpass. Owner: Aarya.

Plan:
  1. build_overpass_query(stations, radius_m) -> Overpass QL for amenity/shop tags around stations
  2. fetch + cache raw JSON in data/raw/osm/
  3. to_amenity(): element -> contracts.Amenity (wheelchair tag missing => "unknown")
  4. category mapping lives in normalize_category() -- see docs/TEAM_PLAN.md learning swap.
"""

from __future__ import annotations

from typing import Any

from ..contracts import Amenity, AmenityCategory, Station


def build_overpass_query(stations: list[Station], radius_m: int = 400) -> str:
    raise NotImplementedError("TODO(Aarya): Overpass QL around station coordinates, batched")


def normalize_category(tags: dict[str, str]) -> AmenityCategory:
    """OSM tags -> AmenityCategory. TODO(Aarya); agree the category list with Deepti (parser) first.

    Examples to handle: amenity=restaurant|fast_food|cafe|toilets, shop=supermarket|convenience,
    tourism=hotel|motel. Unknown => OTHER.
    """
    raise NotImplementedError("TODO(Aarya): OSM tags -> AmenityCategory")


def to_amenity(element: dict[str, Any], retrieved_at: str) -> Amenity:
    raise NotImplementedError("TODO(Aarya): Overpass element -> Amenity")
