"""Provenance and evidence contracts.

Producer/owner: Aarya. Consumers: everyone (explanations, grounding metrics, data-status labels).
Changing this file needs an ADR in docs/decisions/ and 2 approvals (see CONTRIBUTING.md).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class DataStatus(str, Enum):
    """How a fact reached the system (proposal §II: static / cached / runtime)."""

    STATIC = "static"  # curated reference data, e.g. the vehicle table
    CACHED = "cached"  # frozen snapshot: AFDC, OSM, cached OSRM routes
    RUNTIME = "runtime"  # fetched live while answering this request


SourceName = Literal[
    "AFDC", "FUELECONOMY", "MANUFACTURER", "OSM", "OSRM", "DERIVED", "SYNTHETIC"
]


class Provenance(BaseModel):
    source: SourceName
    source_record_id: str | None = None
    source_url: str | None = None
    retrieved_at: datetime
    source_updated_at: datetime | None = None  # e.g. AFDC "updated_at"; None when unknown
    status: DataStatus


class Evidence(BaseModel):
    """One atomic, citable fact. Grounded explanations may only cite these."""

    evidence_id: str
    subject_id: str  # station_id / vehicle_id / amenity_id / itinerary_id
    field: str  # e.g. "ports[0].power_kw", "total_charge_min"
    value: Any
    provenance: Provenance


class Claim(BaseModel):
    """An atomic sentence in an explanation, with the evidence ids that support it."""

    text: str
    supported_by: list[str] = Field(default_factory=list)
