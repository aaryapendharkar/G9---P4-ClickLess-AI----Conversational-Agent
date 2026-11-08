"""Departure-charge targets for normal and comfort-profile planning.

Search and final simulation both call this policy so they agree on charging
time, departure charge, and downstream state of charge.

Owner: Hriday.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from ..contracts import TripRequest


def departure_soc_target(
    request: TripRequest,
    satisfied_preference_keys: Collection[str],
    just_enough_soc_pct: float,
    energy_config: dict[str, Any],
) -> float:
    """Return the selected departure charge for one charging stop.

    Balanced and fastest profiles retain the energy model's just-enough target.
    Comfort may top up to the efficient charging threshold when this stop
    satisfies food that the driver requested.
    """

    comfort_meal_stop = (
        request.tradeoff_profile == "comfort" and "food" in satisfied_preference_keys
    )

    if not comfort_meal_stop:
        return just_enough_soc_pct

    efficient_top_up_soc_pct = energy_config["charging_model"]["taper_start_soc_pct"]
    maximum_departure_soc_pct = energy_config["charge_target_max_soc_pct"]

    return min(
        maximum_departure_soc_pct,
        max(
            just_enough_soc_pct,
            efficient_top_up_soc_pct,
        ),
    )
