"""Configuration A parser: regex rules (+ spaCy later). Owner: Deepti.

PLACEHOLDER: handles the fixture phrasings only, so the walking skeleton runs. The real work:
  * cover the benchmark phrasing variety (see tests/nlu/test_rules_parser.py xfail cases)
  * spaCy NER/matcher for places; gazetteer-backed vehicle matching with model years
  * NEVER invent safety-critical values: leave them None and list them in missing_fields
"""

from __future__ import annotations

import re

from ..contracts import AmenityCategory, AmenityPreference, ParseResult, TripRequest
from ..data import DataStore
from .priority_phrases import detect_profile

_STOP = r"(?=\s+(?:in|with|and|on|via|using|driving)\b|[,.!?;]|$)"
_PLACE = r"([a-z][a-z .'-]*?)"
_OD_PATTERNS = [
    re.compile(rf"\bfrom\s+{_PLACE}\s+to\s+{_PLACE}{_STOP}"),
    re.compile(rf"^(?:drive\s+|driving\s+|trip\s+)?{_PLACE}\s+to\s+{_PLACE}{_STOP}"),
]
_ARRIVAL = re.compile(
    r"arriv\w*(?:\s+\w+){0,3}?\s+with\s+(?:at\s+least\s+|>=?\s*)?(\d{1,3})\s*%"
)
_PCT = re.compile(r"(\d{1,3})\s*%")
_DETOUR = [
    re.compile(r"(\d+)\s*min(?:ute)?s?\s+(?:of\s+)?detour"),
    re.compile(r"detour[^\d]{0,25}(\d+)\s*min"),
]
_AMENITY_KEYWORDS: dict[str, AmenityCategory] = {
    "lunch": AmenityCategory.FOOD, "dinner": AmenityCategory.FOOD, "breakfast": AmenityCategory.FOOD,
    "food": AmenityCategory.FOOD, "eat": AmenityCategory.FOOD, "restaurant": AmenityCategory.FOOD,
    "coffee": AmenityCategory.COFFEE, "cafe": AmenityCategory.COFFEE,
    "restroom": AmenityCategory.RESTROOM, "bathroom": AmenityCategory.RESTROOM,
    "toilet": AmenityCategory.RESTROOM, "grocery": AmenityCategory.GROCERY,
}
_WHEELCHAIR = re.compile(r"wheelchair|accessible|ada\b")


class RulesParser:
    name = "rules"

    def __init__(self, store: DataStore) -> None:
        self.store = store

    def parse(self, text: str, request_id: str) -> ParseResult:
        low = text.strip().lower()
        req = TripRequest(request_id=request_id, raw_text=text)
        ambiguous: dict[str, list[str]] = {}

        for pat in _OD_PATTERNS:
            m = pat.search(low)
            if m:
                req.origin = self.store.geocode(m.group(1))
                req.destination = self.store.geocode(m.group(2))
                break

        vehicles = self.store.find_vehicles(low)
        if len(vehicles) == 1:
            req.vehicle_id = vehicles[0].vehicle_id
        elif len(vehicles) > 1:
            ambiguous["vehicle_id"] = [v.vehicle_id for v in vehicles]

        rest = low
        m = _ARRIVAL.search(low)
        if m:
            req.arrival_soc_pct_min = float(m.group(1))
            rest = low[: m.start()] + low[m.end() :]
        m = _PCT.search(rest)  # PLACEHOLDER: first remaining percentage = starting battery
        if m:
            req.start_soc_pct = float(m.group(1))

        for pat in _DETOUR:
            m = pat.search(low)
            if m:
                req.max_detour_min = float(m.group(1))
                break

        if "fast charg" in low or "dc fast" in low:
            req.min_power_kw = 50.0

        wheelchair = bool(_WHEELCHAIR.search(low))
        seen: set[AmenityCategory] = set()
        for word, cat in _AMENITY_KEYWORDS.items():
            if re.search(rf"\b{word}", low) and cat not in seen:
                seen.add(cat)
                req.amenities.append(
                    AmenityPreference(
                        category=cat,
                        phrase=word,
                        wheelchair_required=wheelchair and cat == AmenityCategory.RESTROOM,
                    )
                )

        req.tradeoff_profile = detect_profile(low)
        missing = [f for f in req.missing_safety_critical() if f not in ambiguous]
        return ParseResult(
            request=req, missing_fields=missing, ambiguous_fields=ambiguous, parser=self.name
        )
