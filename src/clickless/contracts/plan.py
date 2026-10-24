"""Itinerary, cost, trade-off and result contracts.

Producer/owner: Hriday (planner, verifier, ranking, trade-offs).
Consumers: explanation (Deepti), handoff (Aarya), evaluation (Ian).
PlanCost and Alternative are the TA-feedback contracts: every number shown to the driver about a
trade-off must come from these fields (see docs/TRADEOFFS.md).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .data import Connector
from .provenance import Claim
from .request import ClarificationQuestion, TripRequest


class ChargingStop(BaseModel):
    station_id: str
    station_name: str
    connector: Connector
    port_power_kw: float | None
    along_route_km: float
    detour_min: float
    arrive_soc_pct: float
    depart_soc_pct: float
    charge_min_est: float  # an ESTIMATE (no vehicle-specific charging curve)
    amenity_ids: list[str] = Field(default_factory=list)
    # preference key -> best match score at this stop, after the wheelchair rule is applied
    satisfied_preferences: dict[str, float] = Field(default_factory=dict)


class Leg(BaseModel):
    start_label: str
    end_label: str
    distance_km: float
    drive_min: float
    energy_kwh: float
    soc_start_pct: float
    soc_end_pct: float


class Itinerary(BaseModel):
    itinerary_id: str
    request_id: str
    vehicle_id: str
    stops: list[ChargingStop]
    legs: list[Leg]
    total_drive_min: float  # includes detour driving
    total_charge_min: float
    total_detour_min: float
    arrival_soc_pct: float

    @property
    def total_time_min(self) -> float:
        return self.total_drive_min + self.total_charge_min


class PlanCost(BaseModel):
    """Itemised cost in MINUTES-EQUIVALENT. total_min = sum of penalties - credits."""

    profile: str
    drive_min: float
    charge_min: float
    detour_penalty_min: float
    stop_overhead_min: float
    amenity_credit_min: float
    evidence_credit_min: float
    total_min: float
    amenity_satisfaction: float  # mean best-match over requested preferences, 1.0 if none


class Alternative(BaseModel):
    label: str  # "Recommended" | "Fastest" | "Best amenities" | ...
    itinerary: Itinerary
    cost: PlanCost
    # alt minus recommended, e.g. {"total_time_min": -9.0, "detour_min": -4.0, ...}
    deltas: dict[str, float] = Field(default_factory=dict)
    gains: list[str] = Field(default_factory=list)  # preference keys this alt satisfies and Recommended does not
    losses: list[str] = Field(default_factory=list)


ViolationCode = Literal[
    "connector",
    "power",
    "detour",
    "arrival_soc",
    "reserve",
    "status",
    "access",
    "departure_soc",
    "continuity",
    "unknown_station",
]


class Violation(BaseModel):
    code: ViolationCode
    detail: str
    stop_index: int | None = None


class VerificationReport(BaseModel):
    ok: bool
    violations: list[Violation] = Field(default_factory=list)


class Relaxation(BaseModel):
    changes: list[str]  # e.g. ["drop amenity: food", "max_detour_min 10 -> 15"]
    penalty: float
    alternatives: list[Alternative] = Field(default_factory=list)


ResultStatus = Literal["ok", "needs_clarification", "rejected_incomplete", "infeasible", "error"]


class PlanResult(BaseModel):
    request_id: str
    config: str
    status: ResultStatus
    request: TripRequest | None = None
    alternatives: list[Alternative] = Field(default_factory=list)
    relaxation: Relaxation | None = None
    clarification: ClarificationQuestion | None = None
    verification: VerificationReport | None = None
    explanation: str = ""
    claims: list[Claim] = Field(default_factory=list)
    handoff_urls: list[str] = Field(default_factory=list)  # empty until the user approves
    approved: bool = False
    timings_ms: dict[str, float] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
