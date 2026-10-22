"""Stage interfaces (Fig. 1). Each config (A/B/C) is just a different choice of implementation.

Swapping an implementation must never require editing another module -- only configs/*.yaml and
the registry in clickless/pipeline.py. Changing a signature here is a contract change (ADR + 2
approvals).
"""

from __future__ import annotations

from typing import Protocol

from .candidate import Candidate
from .data import Amenity, Vehicle
from .plan import Alternative, Itinerary, PlanCost, PlanResult, VerificationReport
from .provenance import Claim
from .request import AmenityPreference, ClarificationQuestion, ParseResult, Place, TripRequest
from .route import Route


class RequestParser(Protocol):  # Deepti: rules (A) / LLM (B, C)
    name: str

    def parse(self, text: str, request_id: str) -> ParseResult: ...


class Clarifier(Protocol):  # Deepti (C only)
    def question_for(self, parse: ParseResult) -> ClarificationQuestion: ...


class Router(Protocol):  # Hriday: cached fixture/snapshot routes or runtime OSRM
    def route(self, origin: Place, destination: Place) -> Route: ...

    def detour_min(self, route: Route, candidate: Candidate) -> float: ...


class CandidateRetriever(Protocol):  # Manas: spatial corridor retrieval
    def candidates(self, route: Route, request: TripRequest) -> list[Candidate]: ...


class PreferenceMatcher(Protocol):  # Manas: TF-IDF (A) / Sentence-BERT (B, C)
    def score(self, preference: AmenityPreference, amenity: Amenity) -> float: ...


class KGEnricher(Protocol):  # Manas (C only)
    def enrich(self, candidates: list[Candidate], request: TripRequest) -> list[Candidate]: ...


class HardConstraintFilter(Protocol):  # Hriday
    def apply(
        self, candidates: list[Candidate], request: TripRequest, vehicle: Vehicle
    ) -> tuple[list[Candidate], list[tuple[Candidate, str]]]: ...


class Planner(Protocol):  # Hriday
    def plan(
        self, request: TripRequest, vehicle: Vehicle, route: Route, candidates: list[Candidate]
    ) -> list[Itinerary]: ...


class Verifier(Protocol):  # Hriday -- must recheck from scratch, not trust the planner
    def verify(
        self, itinerary: Itinerary, request: TripRequest, vehicle: Vehicle
    ) -> VerificationReport: ...


class Ranker(Protocol):  # Hriday: logistic regression (A) / weighted (B) / weighted+evidence (C)
    def rank(
        self, itineraries: list[Itinerary], request: TripRequest
    ) -> list[tuple[Itinerary, PlanCost]]: ...


class TradeoffSelector(Protocol):  # Hriday (TA feedback)
    def select(
        self, ranked: list[tuple[Itinerary, PlanCost]], request: TripRequest
    ) -> list[Alternative]: ...


class Explainer(Protocol):  # Deepti: template (A, B) / grounded LLM (C)
    def explain(self, result: PlanResult) -> tuple[str, list[Claim]]: ...


class HandoffBuilder(Protocol):  # Aarya: Google Maps URLs, only after approval
    def build(self, itinerary: Itinerary, route: Route) -> list[str]: ...
