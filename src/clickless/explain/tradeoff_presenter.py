"""Turn trade-off numbers into driver-facing text (TA feedback: "how trade-offs are presented").

Owners: Deepti (module) + Hriday (LEARNING SWAP: he writes the templates, Deepti reviews).
Hard rule: every number printed here comes straight from Alternative.deltas / PlanCost fields --
nothing is recomputed or estimated in text. That is what makes trade-off statements checkable
by the grounding metric (Ian) and keeps C's LLM explanation honest.

PLACEHOLDER output is deliberately plain. TODO(Hriday, learning swap):
  * natural sentences: "Fastest: 9 min quicker, but no lunch spot within 400 m of the charger"
  * pick the 1-2 deltas that matter (ignore |delta| < 1 min), round sensibly
  * phrase estimates as estimates ("about 18 min of charging, estimated")
  * offer the follow-up: "Say 'prioritize food' or pick an option"
"""

from __future__ import annotations

from ..contracts import Alternative, PlanCost


def render_cost_breakdown(cost: PlanCost) -> str:
    return (
        f"Why this ranks first ({cost.profile} priorities, in minutes-equivalent): "
        f"driving {cost.drive_min:.0f} + charging {cost.charge_min:.0f} + detour penalty "
        f"{cost.detour_penalty_min:.0f} + stop overhead {cost.stop_overhead_min:.0f} "
        f"- requested amenities {cost.amenity_credit_min:.0f} = {cost.total_min:.0f}."
    )


def render_alternative(alt: Alternative) -> str:
    d = alt.deltas
    parts = [f"{d.get('total_time_min', 0):+.0f} min total",
             f"{d.get('detour_min', 0):+.0f} min detour",
             f"{d.get('charge_min', 0):+.0f} min charging"]
    if alt.gains:
        parts.append("adds " + ", ".join(alt.gains))
    if alt.losses:
        parts.append("loses " + ", ".join(alt.losses))
    stops = ", ".join(s.station_name for s in alt.itinerary.stops) or "no charging stop"
    return f"{alt.label} ({stops}): " + "; ".join(parts) + "."
