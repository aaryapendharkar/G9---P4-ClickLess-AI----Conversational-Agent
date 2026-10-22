"""TripRequest contract: the output of request understanding (rules / spaCy / LLM).

Producer/owner: Deepti. Consumers: pipeline, planning, ranking, evaluation (gold annotations use
the same fields, so the annotation guide and this file must stay in sync).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from .data import AmenityCategory, Connector

# Fields we never fill with a default. Missing => reject (Config A/B) or clarify (Config C).
SAFETY_CRITICAL_FIELDS: tuple[str, ...] = ("origin", "destination", "vehicle_id", "start_soc_pct")

TradeoffProfile = Literal["balanced", "fastest", "comfort"]


class Place(BaseModel):
    text: str
    lat: float | None = None
    lon: float | None = None

    @property
    def resolved(self) -> bool:
        return self.lat is not None and self.lon is not None


class AmenityPreference(BaseModel):
    category: AmenityCategory
    phrase: str = ""  # the user's own words, used by TF-IDF / SBERT matching
    wheelchair_required: bool = False
    priority: Literal["nice_to_have", "important"] = "nice_to_have"

    @property
    def key(self) -> str:
        """Stable key used by ranking weights and annotations, e.g. 'food', 'restroom_wheelchair'."""
        base = self.category.value
        return f"{base}_wheelchair" if self.wheelchair_required else base


class TripRequest(BaseModel):
    request_id: str
    raw_text: str
    origin: Place | None = None
    destination: Place | None = None
    vehicle_id: str | None = None
    start_soc_pct: float | None = Field(default=None, ge=0, le=100)
    arrival_soc_pct_min: float | None = Field(default=None, ge=0, le=100)
    connectors_override: list[Connector] | None = None
    max_detour_min: float | None = None
    min_power_kw: float | None = None
    amenities: list[AmenityPreference] = Field(default_factory=list)
    # Trade-off controls (TA feedback): a named profile plus optional per-term overrides,
    # e.g. {"amenity_value_min.food": 25}. See docs/TRADEOFFS.md.
    tradeoff_profile: TradeoffProfile = "balanced"
    weight_overrides: dict[str, float] = Field(default_factory=dict)

    def missing_safety_critical(self) -> list[str]:
        missing = []
        for name in SAFETY_CRITICAL_FIELDS:
            value = getattr(self, name)
            if value is None or (isinstance(value, Place) and not value.resolved):
                missing.append(name)
        return missing


class ParseResult(BaseModel):
    request: TripRequest
    missing_fields: list[str] = Field(default_factory=list)
    ambiguous_fields: dict[str, list[str]] = Field(default_factory=dict)  # field -> candidates
    parser: str  # "rules" | "spacy" | "llm"

    @property
    def needs_input(self) -> bool:
        return bool(self.missing_fields or self.ambiguous_fields)


class ClarificationQuestion(BaseModel):
    field: str
    question: str
    options: list[str] = Field(default_factory=list)
