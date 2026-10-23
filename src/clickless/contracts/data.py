"""Station, vehicle and amenity contracts.

Producer/owner: Aarya (ingestion + normalization). Consumers: retrieval, planning, oracle, handoff.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field

from .provenance import Provenance


class Connector(str, Enum):
    CCS1 = "CCS1"
    NACS = "NACS"
    CHADEMO = "CHADEMO"
    J1772 = "J1772"


class ChargerLevel(str, Enum):
    DC_FAST = "dc_fast"
    L2 = "l2"
    L1 = "l1"


class StationStatus(str, Enum):
    AVAILABLE = "available"
    PLANNED = "planned"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"
    UNKNOWN = "unknown"


class ChargingPort(BaseModel):
    connector: Connector
    level: ChargerLevel
    power_kw: float | None = None  # None = not reported by the source
    count: int = 1


class Station(BaseModel):
    station_id: str
    name: str
    lat: float
    lon: float
    network: str | None = None
    access: Literal["public", "restricted"] = "public"
    status: StationStatus = StationStatus.UNKNOWN
    ports: list[ChargingPort]
    provenance: Provenance


class AmenityCategory(str, Enum):
    FOOD = "food"
    COFFEE = "coffee"
    RESTROOM = "restroom"
    GROCERY = "grocery"
    LODGING = "lodging"
    OTHER = "other"


class Amenity(BaseModel):
    amenity_id: str  # e.g. "osm:node/123"
    name: str | None = None
    category: AmenityCategory
    lat: float
    lon: float
    # Missing OSM wheelchair tag => "unknown", NEVER treated as accessible (proposal §II).
    wheelchair: Literal["yes", "limited", "no", "unknown"] = "unknown"
    opening_hours: str | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    provenance: Provenance


class Vehicle(BaseModel):
    """One supported model-year variant. Every curated value carries its own provenance."""

    vehicle_id: str
    make: str
    model: str
    model_year: int
    variant: str | None = None
    usable_kwh: float
    efficiency_kwh_per_km: float
    native_connectors: list[Connector]
    adapters: list[Connector] = Field(default_factory=list)  # documented adapters only
    max_dc_kw: float | None = None
    aliases: list[str] = Field(default_factory=list)  # lowercase phrases users might type
    field_provenance: dict[str, Provenance] = Field(default_factory=dict)

    def supported_connectors(self) -> set[Connector]:
        return set(self.native_connectors) | set(self.adapters)
