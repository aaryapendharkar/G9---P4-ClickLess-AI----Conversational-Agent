"""Turn trade-off numbers into driver-facing text (TA feedback: "how trade-offs are presented").

Owners: Deepti (module) + Hriday (LEARNING SWAP: he writes the templates, Deepti reviews).
Hard rule: every number printed here comes straight from Alternative.deltas / PlanCost fields --
nothing is recomputed or estimated in text. That is what makes trade-off statements checkable
by the grounding metric (Ian) and keeps C's LLM explanation honest.

The presenter translates signed deltas into natural directions, suppresses
sub-minute differences, shows at most two numerical differences, labels charging
time as an estimate, and translates internal preference keys. The surrounding
template owns the follow-up prompt that lets a driver re-rank the options.
"""

from __future__ import annotations

from ..contracts import Alternative, PlanCost

_PREFERENCE_LABELS = {
    "food": "a lunch spot",
    "restroom_wheelchair": "an accessible restroom",
}


def _driver_facing_preferences(preference_keys: list[str]) -> str:
    """Translate internal preference keys into language shown to drivers."""

    readable_preferences = [
        _PREFERENCE_LABELS.get(
            preference_key,
            preference_key.replace("_", " "),
        )
        for preference_key in preference_keys
    ]
    return ", ".join(readable_preferences)


def _describe_minute_delta(
    delta_name: str,
    difference_min: float,
) -> str | None:
    """Turn one meaningful signed delta into driver-facing language."""

    if abs(difference_min) < 1:
        return None

    rounded_difference_min = abs(difference_min)

    if delta_name == "total_time_min":
        direction = "quicker" if difference_min < 0 else "longer"
        return f"about {rounded_difference_min:.0f} min {direction}"

    if delta_name == "detour_min":
        direction = "less detour" if difference_min < 0 else "more detour"
        return f"about {rounded_difference_min:.0f} min {direction}"

    if delta_name == "charge_min":
        direction = "less charging" if difference_min < 0 else "more charging"
        return f"about {rounded_difference_min:.0f} min " f"{direction} (estimate)"

    return None


def render_cost_breakdown(cost: PlanCost) -> str:
    """Explain the recommended plan using its stored minutes-equivalent terms."""

    return (
        f"Why this ranks first ({cost.profile} priorities, in minutes-equivalent): "
        f"driving {cost.drive_min:.0f} + charging {cost.charge_min:.0f} + detour penalty "
        f"{cost.detour_penalty_min:.0f} + stop overhead {cost.stop_overhead_min:.0f} "
        f"- requested amenities {cost.amenity_credit_min:.0f} = {cost.total_min:.0f}."
    )


def render_alternative(alternative: Alternative) -> str:
    """Describe meaningful contract deltas without recalculating plan values."""

    minute_differences = [
        description
        for delta_name in (
            "total_time_min",
            "detour_min",
            "charge_min",
        )
        if (
            description := _describe_minute_delta(
                delta_name,
                alternative.deltas.get(delta_name, 0.0),
            )
        )
        is not None
    ]

    # Two numerical comparisons are enough to explain why the option differs
    # without overwhelming the driver. Amenity gains and losses remain visible
    # because they explain what the extra or saved time buys.
    details = minute_differences[:2]

    if alternative.gains:
        details.append("adds " + _driver_facing_preferences(alternative.gains))

    if alternative.losses:
        details.append("loses " + _driver_facing_preferences(alternative.losses))

    if alternative.itinerary.stops:
        station_names = ", ".join(
            stop.station_name for stop in alternative.itinerary.stops
        )
        route_change = f"stop at {station_names} instead"
    else:
        route_change = "drive directly instead"

    description = "; ".join(details) if details else "no material difference recorded"

    return f"{alternative.label}: {route_change}; {description}."
