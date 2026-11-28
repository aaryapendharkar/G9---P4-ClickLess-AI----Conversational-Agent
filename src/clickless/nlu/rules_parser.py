"""Configuration A parser: deterministic rules with datastore grounding. Owner: Deepti.

The parser extracts only information present in the request. Unknown places and vague
safety-critical battery language remain unresolved so the pipeline can reject or clarify them.
"""

from __future__ import annotations

import re

from ..contracts import AmenityCategory, AmenityPreference, ParseResult, TripRequest
from ..data import DataStore
from ..data.place_names import STATE_CODES
from .connector_normalization import CONNECTOR_ALIASES, normalize_connector
from .priority_phrases import detect_priority_keys, detect_profile, detect_weight_overrides

_STOP = r"(?=\s+(?:in|with|and|on|via|using|driving)\b|[,.!?;]|$)"
# A place may carry its state after a comma ("Tempe, Arizona", "Tempe, AZ"). Any other comma still
# ends the place. DataStore.geocode normalizes the state spelling.
_STATES = "|".join(
    re.escape(state)
    for state in sorted((*STATE_CODES, *STATE_CODES.values()), key=len, reverse=True)
)
_PLACE = rf"([a-z][a-z .'-]*?(?:,\s*(?:{_STATES})\b)?)"
# Typed in lower case after a comma, these postal codes are ordinary words (", ok", ", me and ...").
_COMMA_BEFORE_WORD = re.compile(r",(?=\s*(?:hi|in|me|oh|ok|or)\b)")
_CONNECTOR_NAMES = "|".join(
    r"\s+".join(re.escape(word) for word in name.split())
    for name in sorted(CONNECTOR_ALIASES, key=len, reverse=True)
)
_CONNECTOR_RESTRICTION = re.compile(
    r"(?<!\bnot )(?<!\bnever )(?<!\bdont )(?<!n't )(?<!n’t )"
    rf"\buse\s+only\s+(?P<connector>{_CONNECTOR_NAMES})\b",
    re.IGNORECASE,
)
_OD_PATTERNS = [
    re.compile(rf"\bfrom\s+{_PLACE}\s+to\s+{_PLACE}{_STOP}"),
    # A leading clause such as "I'm in a hurry: Phoenix to Flagstaff" is allowed.
    re.compile(rf"(?:^|:\s*){_PLACE}\s+to\s+{_PLACE}{_STOP}"),
    re.compile(rf"^(?:drive\s+|driving\s+|trip\s+)?{_PLACE}\s+to\s+{_PLACE}{_STOP}"),
]
_ARRIVAL_PATTERNS = (
    re.compile(
        r"arriv\w*(?:\s+\w+){0,3}?\s+with\s+(?:at\s+least\s+|>=?\s*)?(\d{1,3})\s*(?:%|percent)"
    ),
    re.compile(
        r"(?:with\s+)?(?:at\s+least\s+|>=?\s*)(\d{1,3})\s*(?:%|percent)\s+(?:when\s+i\s+arrive|on\s+arrival)"
    ),
)
_PCT = re.compile(r"(\d{1,3})\s*(?:%|percent)")
_VAGUE_BATTERY = re.compile(
    r"\b(?:half(?:\s+a)?\s+tank|half\s+full|mostly\s+full|low\s+battery|some\s+charge)\b"
)
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

        connector_match = _CONNECTOR_RESTRICTION.search(low)
        if connector_match:
            req.connectors_override = [normalize_connector(connector_match.group("connector"))]

        route_text = _COMMA_BEFORE_WORD.sub(";", text.strip()).lower()
        for pat in _OD_PATTERNS:
            m = pat.search(route_text)
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
        arrival_match = None
        for arrival_pattern in _ARRIVAL_PATTERNS:
            arrival_match = arrival_pattern.search(low)
            if arrival_match:
                req.arrival_soc_pct_min = float(arrival_match.group(1))
                rest = low[: arrival_match.start()] + low[arrival_match.end() :]
                break
        percentage_match = _PCT.search(rest)
        if percentage_match:
            req.start_soc_pct = float(percentage_match.group(1))
        elif _VAGUE_BATTERY.search(rest):
            # Do not guess a safety-critical battery value from vague language.
            ambiguous["start_soc_pct"] = ["an exact percentage is required"]

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
        req.weight_overrides = detect_weight_overrides(low)
        priority_keys = detect_priority_keys(low)
        for preference in req.amenities:
            if preference.key in priority_keys or preference.category.value in priority_keys:
                preference.priority = "important"
        missing = [field for field in req.missing_safety_critical() if field not in ambiguous]
        return ParseResult(
            request=req, missing_fields=missing, ambiguous_fields=ambiguous, parser=self.name
        )
