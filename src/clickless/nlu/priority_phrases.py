"""Map what the driver says about priorities to a trade-off profile (TA feedback). Owner: Deepti.

This module keeps the deterministic profile vocabulary small and explicit. It never treats a
negated phrase as a positive preference, and it only emits override keys accepted by the ranking
configuration.
"""

from __future__ import annotations

import re

from ..contracts.request import TradeoffProfile

_FASTEST = ("hurry", "fastest", "asap", "quickest", "as fast as", "save time")
_COMFORT = ("don't mind", "dont mind", "no rush", "take it easy", "relaxed", "longer stop")
_PRIORITY_PHRASES = {
    "food": ("food matters more", "prioritize food", "prioritise food", "need food", "want food"),
    "coffee": ("coffee matters more", "prioritize coffee", "prioritise coffee"),
    "restroom": ("restroom matters more", "prioritize restroom", "prioritise restroom"),
    "restroom_wheelchair": (
        "accessible restroom matters more",
        "prioritize an accessible restroom",
        "prioritise an accessible restroom",
        "want an accessible restroom",
    ),
}


def _has_affirmative_phrase(text: str, phrase: str) -> bool:
    """Match an explicit phrase unless its nearby context negates the request."""
    for match in re.finditer(re.escape(phrase), text):
        preceding_text = text[max(0, match.start() - 40):match.start()]
        if not re.search(r"\b(?:not|no|don't|dont|do not|never)\b", preceding_text):
            return True
    return False


def detect_profile(text: str) -> TradeoffProfile:
    """Return the named profile expressed by the driver's words."""
    low = text.lower()
    if any(_has_affirmative_phrase(low, phrase) for phrase in _FASTEST):
        return "fastest"
    if any(phrase in low for phrase in _COMFORT):
        return "comfort"
    return "balanced"


def detect_weight_overrides(text: str) -> dict[str, float]:
    """Translate explicit amenity priorities into safe, known ranking weights.

    The values are deliberately valid keys from ``configs/tradeoffs.yaml``. A priority
    phrase raises the value of an amenity; it never supplies a safety-critical default.
    """
    low = text.lower()
    overrides: dict[str, float] = {}
    for amenity_key, phrases in _PRIORITY_PHRASES.items():
        if any(_has_affirmative_phrase(low, phrase) for phrase in phrases):
            overrides[f"amenity_value_min.{amenity_key}"] = 30.0 if amenity_key == "food" else 25.0
    return overrides


def detect_priority_keys(text: str) -> set[str]:
    """Return amenity keys explicitly described as important by the driver."""
    low = text.lower()
    return {
        amenity_key
        for amenity_key, phrases in _PRIORITY_PHRASES.items()
        if any(_has_affirmative_phrase(low, phrase) for phrase in phrases)
    }
