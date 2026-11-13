"""Template explanations for Configurations A and B. Owner: Deepti.

All displayed route, charging, cost, and trade-off values come from the verified plan result or
its linked datastore records. The template does not recompute planning decisions.
"""

from __future__ import annotations

from ..contracts import Claim, PlanResult
from ..data import DataStore
from ..data.provenance import status_label
from .tradeoff_presenter import render_alternative, render_cost_breakdown


class TemplateExplainer:
    def __init__(self, store: DataStore, provenance_labels: bool = False) -> None:
        self.store = store
        self.provenance_labels = provenance_labels

    def explain(self, result: PlanResult) -> tuple[str, list[Claim]]:
        if result.status == "needs_clarification" and result.clarification:
            question = result.clarification.question
            options = result.clarification.options
            if options:
                question += "\n" + "\n".join(
                    f"{index}. {option}" for index, option in enumerate(options, start=1)
                )
            return question, []
        if result.status == "rejected_incomplete":
            missing = ", ".join(result.notes) or "some trip details"
            return f"I can't plan this safely yet -- missing: {missing}.", []
        if result.status == "infeasible":
            text = "I couldn't find a plan that meets all your requirements with this vehicle and data."
            if result.relaxation:
                text += " If you allow: " + "; ".join(result.relaxation.changes) + ", a plan exists."
            return text, []
        if result.status != "ok" or not result.alternatives:
            return "Something went wrong while planning.", []

        rec = result.alternatives[0]
        it, req = rec.itinerary, result.request
        lines = []
        if not it.stops:
            lines.append(f"No charging needed: drive straight to {req.destination.text}.")
        for s in it.stops:
            power = f"{s.port_power_kw:.0f} kW" if s.port_power_kw else "unknown power"
            lines.append(
                f"Stop at {s.station_name} ({s.connector.value}, {power}): charge "
                f"{s.arrive_soc_pct:.0f}% -> {s.depart_soc_pct:.0f}% in about {s.charge_min_est:.0f} min (estimate)."
            )
            if self.provenance_labels:
                lines.append("  Source: " + status_label(self.store.station(s.station_id).provenance))
            names = [a.name for a in self.store.amenities if a.amenity_id in s.amenity_ids]
            if names:
                lines.append("  Nearby (requested): " + ", ".join(n for n in names if n))
        lines.append(
            f"Arrive in {req.destination.text} with about {it.arrival_soc_pct:.0f}% after "
            f"~{it.total_time_min:.0f} min total ({it.total_detour_min:.0f} min of detour)."
        )
        lines.append(render_cost_breakdown(rec.cost))
        skipped_stations = [note.removeprefix("skipped ") for note in result.notes if note.startswith("skipped ")]
        if skipped_stations:
            lines.append("Not used: " + "; ".join(skipped_stations) + ".")
        if len(result.alternatives) > 1:
            lines.append("Other options:")
            lines += ["  " + render_alternative(alternative) for alternative in result.alternatives[1:]]
            lines.append("Say 'prioritize food' or choose an option to change the ranking.")
        lines.append("Open this route in Google Maps? (approval required)")
        return "\n".join(lines), []
