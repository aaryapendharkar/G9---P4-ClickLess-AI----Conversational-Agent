"""Shared contracts (v0.1). Every module talks to every other module ONLY through these types.

Rules (see CONTRIBUTING.md):
  * each file is owned by the module that PRODUCES the type;
  * changes need an ADR in docs/decisions/ + 2 approvals, and bump CONTRACTS_VERSION;
  * additive optional fields are cheap; renames/removals are expensive -- avoid them.
"""

from .candidate import Candidate, NearbyAmenity
from .data import (
    Amenity,
    AmenityCategory,
    ChargerLevel,
    ChargingPort,
    Connector,
    Station,
    StationStatus,
    Vehicle,
)
from .plan import (
    Alternative,
    ChargingStop,
    Itinerary,
    Leg,
    PlanCost,
    PlanResult,
    Relaxation,
    VerificationReport,
    Violation,
)
from .provenance import Claim, DataStatus, Evidence, Provenance
from .request import (
    SAFETY_CRITICAL_FIELDS,
    AmenityPreference,
    ClarificationQuestion,
    ParseResult,
    Place,
    TripRequest,
)
from .route import LatLon, Route

CONTRACTS_VERSION = "0.1.0"

__all__ = [
    "CONTRACTS_VERSION", "SAFETY_CRITICAL_FIELDS",
    "Alternative", "Amenity", "AmenityCategory", "AmenityPreference", "Candidate", "ChargerLevel",
    "ChargingPort", "ChargingStop", "Claim", "ClarificationQuestion", "Connector", "DataStatus",
    "Evidence", "Itinerary", "LatLon", "Leg", "NearbyAmenity", "ParseResult", "Place", "PlanCost",
    "PlanResult", "Provenance", "Relaxation", "Route", "Station", "StationStatus", "TripRequest",
    "Vehicle", "VerificationReport", "Violation",
]
