"""Configuration A ranker: interpretable logistic regression (proposal §III-A). Owner: Hriday.

Features: route duration, number of stops, detour, amenity coverage (+ charge minutes, TA).
Trained on DEV candidate plans labelled good/bad; evaluated with nDCG@3 against separate graded
labels. Until a model file exists, falls back to the weighted cost so the skeleton still runs
(and the pipeline adds a note to the result saying so).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..contracts import Itinerary, PlanCost, TripRequest
from .cost import plan_cost
from .weighted_ranker import WeightedRanker

FEATURES = ["total_time_min", "n_stops", "detour_min", "charge_min", "amenity_satisfaction"]


def features(itinerary: Itinerary, cost: PlanCost) -> list[float]:
    return [itinerary.total_time_min, float(len(itinerary.stops)), itinerary.total_detour_min,
            itinerary.total_charge_min, cost.amenity_satisfaction]


class LogRegRanker:
    def __init__(self, tradeoffs_cfg: dict[str, Any], model_path: str = "results/models/logreg_a.joblib") -> None:
        self.cfg = tradeoffs_cfg
        self.model_path = Path(model_path)
        self.model = None  # TODO(Hriday): load if self.model_path exists
        self.fallback_used = False

    def fit(self, X: list[list[float]], y: list[int]) -> None:
        raise NotImplementedError("TODO(Hriday): standardize + LogisticRegression, save with joblib")

    def rank(self, itineraries: list[Itinerary], request: TripRequest) -> list[tuple[Itinerary, PlanCost]]:
        if self.model is None:
            self.fallback_used = True
            return WeightedRanker(self.cfg).rank(itineraries, request)
        scored = [(it, plan_cost(it, request, self.cfg)) for it in itineraries]
        probs = self.model.predict_proba([features(it, c) for it, c in scored])[:, 1]
        order = sorted(range(len(scored)), key=lambda i: -probs[i])
        return [scored[i] for i in order]
