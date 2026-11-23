"""Template explanations for Configurations A and B. Owner: Deepti.

All displayed route, charging, cost, and trade-off values come from the verified plan result or
its linked datastore records. The template does not recompute planning decisions.
"""

from __future__ import annotations

from ..common.errors import NO_CACHED_ROUTE_NOTE
from ..contracts import Amenity, AmenityCategory, Claim, PlanResult, TripRequest
from ..data import DataStore
from ..data.provenance import status_label
from .tradeoff_presenter import render_alternative, render_cost_breakdown


class TemplateExplainer:
    def __init__(self, store: DataStore, provenance_labels: bool = False) -> None:
        self.store = store
        self.provenance_labels = provenance_labels
        self._amenities_by_id: dict[str, Amenity] | None = None

    @property
    def amenities_by_id(self) -> dict[str, Amenity]:
        if self._amenities_by_id is None:
            self._amenities_by_id = {amenity.amenity_id: amenity for amenity in self.store.amenities}
        return self._amenities_by_id

    def _connector_infeasibility(self, result: PlanResult) -> str | None:
        """Explain a connector dead-end using only the grounded vehicle record."""
        request = result.request
        if request is None or not request.vehicle_id or not request.connectors_override:
            return None
        vehicle = self.store.vehicle(request.vehicle_id)
        requested = {connector.value for connector in request.connectors_override}
        supported = {connector.value for connector in vehicle.supported_connectors()}
        if requested & supported:
            return None
        vehicle_name = self.store.vehicle_label(request.vehicle_id)
        requested_text = ", ".join(sorted(requested))
        supported_text = ", ".join(sorted(supported)) or "none listed"
        return (
            f"{vehicle_name} cannot use the requested connector(s): {requested_text}. "
            f"This vehicle supports: {supported_text}. Try one of the supported connectors."
        )

    def explain(self, result: PlanResult) -> tuple[str, list[Claim]]:
        if result.status == "needs_clarification" and result.clarification:
            question = result.clarification.question
            options = result.clarification.options
            if options:
                display_options = options
                if result.clarification.field == "vehicle_id":
                    display_options = [
                        self.store.vehicle_label(option)
                        if option in self.store.vehicles
                        else option
                        for option in options
                    ]
                question += "\n" + "\n".join(
                    f"{index}. {option}"
                    for index, option in enumerate(display_options, start=1)
                )
            return question, []
        if result.status == "rejected_incomplete":
            missing = ", ".join(result.notes) or "some trip details"
            return f"I can't plan this safely yet -- missing: {missing}.", []
        if result.status == "infeasible":
            connector_reason = self._connector_infeasibility(result)
            if connector_reason:
                text = connector_reason
            else:
                text = "I couldn't find a plan that meets all your requirements with this vehicle and data."
            if result.relaxation:
                text += " If you allow: " + "; ".join(result.relaxation.changes) + ", a plan exists."
            return text, []
        if result.status == "error" and result.request and _route_not_cached(result):
            trip = f"{result.request.origin.text} -> {result.request.destination.text}"
            return f"I don't have route data for {trip} yet, so I can't plan this trip offline.", []
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
            nearby = requested_amenity_labels(s.amenity_ids, self.amenities_by_id, req)
            if nearby:
                lines.append("  Nearby (requested): " + ", ".join(label for label, _ in nearby))
        lines.append(
            f"Arrive in {req.destination.text} with about {it.arrival_soc_pct:.0f}% after "
            f"~{it.total_time_min:.0f} min total ({it.total_detour_min:.0f} min of detour)."
        )
        lines.append(render_cost_breakdown(rec.cost))
        skipped_summary = _skipped_summary(result.notes)
        if skipped_summary:
            lines.append(skipped_summary)
        if len(result.alternatives) > 1:
            lines.append("Other options:")
            lines += ["  " + render_alternative(alternative) for alternative in result.alternatives[1:]]
            lines.append("Say 'prioritize food' or choose an option to change the ranking.")
        lines.append("Open this route in Google Maps? (approval required)")
        return "\n".join(lines), []


def _route_not_cached(result: PlanResult) -> bool:
    return any(note.startswith(NO_CACHED_ROUTE_NOTE) for note in result.notes)


# Driver-facing wording for the hard-constraint filter's reasons (planning/filters.py).
_SKIP_REASON_LABELS = {
    "access:restricted": "restricted access",
    "connector": "incompatible connector",
    "power": "below your minimum power",
    "detour": "detour too long",
}


def _skipped_summary(notes: list[str]) -> str | None:
    """One line for the stations the filter ruled out, counted by reason (#139).

    The per-station "skipped <id>: <reason>" notes stay in PlanResult.notes for evaluation; the
    driver only sees the counts.
    """
    counts: dict[str, int] = {}
    for note in notes:
        if note.startswith("skipped "):
            reason = note.rsplit(": ", 1)[-1]
            label = _SKIP_REASON_LABELS.get(reason) or reason.removeprefix("status:").replace("_", " ")
            counts[label] = counts.get(label, 0) + 1
    if not counts:
        return None
    total = sum(counts.values())
    by_reason = ", ".join(f"{n} {label}" for label, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
    return f"Not used: {total} station{'s' if total != 1 else ''} on the route ({by_reason})."


# How an amenity with no OSM name is shown: by its category (#137).
_UNNAMED_NOUNS = {
    AmenityCategory.FOOD: "place to eat",
    AmenityCategory.COFFEE: "coffee shop",
    AmenityCategory.RESTROOM: "restroom",
    AmenityCategory.GROCERY: "grocery store",
    AmenityCategory.LODGING: "place to stay",
    AmenityCategory.OTHER: "amenity",
}


def amenity_label(amenity: Amenity) -> str:
    """The amenity's name, or for an unnamed one its category, e.g. "an accessible restroom".

    "accessible" is only said for an explicit wheelchair=yes (ADR 0011: unknown is never accessible).
    """
    if amenity.name:
        return amenity.name
    noun = _UNNAMED_NOUNS[amenity.category]
    if amenity.wheelchair == "yes":
        return f"an accessible {noun}"
    return f"{'an' if noun[0] in 'aeiou' else 'a'} {noun}"


def requested_amenity_labels(
    amenity_ids: list[str], amenities_by_id: dict[str, Amenity], request: TripRequest | None
) -> list[tuple[str, Amenity]]:
    """(label, amenity) for a stop's matched amenities, in the order the driver asked for them (#137).

    Amenities are grouped by the request's preferences (in request order), so a requested
    accessible restroom is not pushed to the end behind a list of named cafés. Two unnamed
    amenities with the same label are shown once. Ids missing from the store are left out, so the
    list is empty (and the "Nearby (requested):" line omitted) when there is nothing to show.
    The full amenity_ids stay on the itinerary stop in the saved result.
    """
    amenities = [amenities_by_id[a] for a in amenity_ids if a in amenities_by_id]
    preferences = request.amenities if request is not None else []

    def preference_rank(amenity: Amenity) -> int:
        for rank, preference in enumerate(preferences):
            if preference.category == amenity.category and (
                not preference.wheelchair_required or amenity.wheelchair == "yes"
            ):
                return rank
        return len(preferences)

    labelled: list[tuple[str, Amenity]] = []
    seen: set[str] = set()
    for amenity in sorted(amenities, key=preference_rank):  # stable: keeps the stop's order within a group
        label = amenity_label(amenity)
        if label not in seen:
            seen.add(label)
            labelled.append((label, amenity))
    return labelled
