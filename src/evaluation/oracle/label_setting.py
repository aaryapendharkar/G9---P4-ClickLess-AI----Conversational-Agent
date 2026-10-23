"""Label-setting oracle for model-relative feasibility (proposal §V). Owner: Ian.

Search state (label) at a corridor station: (route position, elapsed time, SoC, path).
Expand to every later compatible station or the destination when the leg is feasible under the
documented model; at each station consider a small set of departure SoCs. Prune labels dominated
on (position, time, SoC). Output per request:
  * feasible: bool  (does ANY itinerary satisfy all hard constraints?)
  * best_time_min and the Pareto set of (time, detour, charge, amenity) for trade-off checks
  * is_valid(itinerary) -> bool, used for "oracle-valid itinerary" and violation rate
Uses the same frozen inputs (DataStore, cached routes) but none of the planner's code.
"""

from __future__ import annotations

from clickless.contracts import Itinerary, TripRequest


class LabelSettingOracle:
    def __init__(self, store, energy_cfg: dict) -> None:
        self.store = store
        self.energy_cfg = energy_cfg

    def is_feasible(self, request: TripRequest) -> bool:
        raise NotImplementedError("TODO(Ian): label-setting search with dominance pruning")

    def is_valid(self, itinerary: Itinerary, request: TripRequest) -> bool:
        raise NotImplementedError("TODO(Ian): independent validity check of a returned itinerary")
