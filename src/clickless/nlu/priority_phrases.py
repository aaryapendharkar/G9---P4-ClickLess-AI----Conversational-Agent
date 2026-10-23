"""Map what the driver says about priorities to a trade-off profile (TA feedback). Owner: Deepti.

PLACEHOLDER keyword version. Target behaviour:
  * "I'm in a hurry" / "fastest" -> fastest
  * "I don't mind a longer stop if there's good food" / "take it easy" -> comfort
  * "food matters more than time" -> weight_overrides {"amenity_value_min.food": ...}
  * follow-ups after seeing alternatives ("the one with the restroom") should re-rank, not re-plan
The LLM parser (B/C) should emit the same fields; evaluate both on the benchmark's priority cases.
"""

from __future__ import annotations

from ..contracts.request import TradeoffProfile

_FASTEST = ("hurry", "fastest", "asap", "quickest", "as fast as")
_COMFORT = ("don't mind", "dont mind", "no rush", "take it easy", "relaxed", "longer stop")


def detect_profile(text: str) -> TradeoffProfile:
    low = text.lower()
    if any(k in low for k in _FASTEST):
        return "fastest"
    if any(k in low for k in _COMFORT):
        return "comfort"
    return "balanced"
