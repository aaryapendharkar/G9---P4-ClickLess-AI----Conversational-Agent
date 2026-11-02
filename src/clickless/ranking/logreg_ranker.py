"""Configuration A's interpretable logistic-regression ranker.

Features are calculated in one documented order and standardized before
logistic regression. A trained preprocessing-and-classifier pipeline is saved
as one joblib artifact so runtime prediction uses the same transformations as
training.

Until a trained model exists, Configuration A safely falls back to the
minutes-equivalent weighted ranker.

Owner: Hriday.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline as SklearnPipeline
from sklearn.preprocessing import StandardScaler

from ..contracts import Itinerary, PlanCost, TripRequest
from .cost import plan_cost
from .weighted_ranker import WeightedRanker

FEATURE_NAMES = [
    "total_time_min",
    "n_stops",
    "detour_min",
    "charge_min",
    "amenity_satisfaction",
]
DEFAULT_MODEL_PATH = "results/models/logreg_a.joblib"


def extract_feature_values(
    itinerary: Itinerary,
    cost: PlanCost,
) -> list[float]:
    """Return one plan's numerical inputs in `FEATURE_NAMES` order."""

    return [
        itinerary.total_time_min,
        float(len(itinerary.stops)),
        itinerary.total_detour_min,
        itinerary.total_charge_min,
        cost.amenity_satisfaction,
    ]


class LogRegRanker:
    """Train, persist, load, and apply Configuration A's ranker."""

    def __init__(
        self,
        tradeoffs_cfg: dict[str, Any],
        model_path: str | Path = DEFAULT_MODEL_PATH,
    ) -> None:
        self.tradeoffs_config = tradeoffs_cfg
        self.model_path = Path(model_path)
        self.model: SklearnPipeline | None = None
        self.fallback_used = False

        if self.model_path.exists():
            self.model = joblib.load(self.model_path)

    def fit(
        self,
        training_features: list[list[float]],
        good_plan_labels: list[int],
    ) -> None:
        """Fit and save a standardized binary logistic-regression model."""

        if not training_features:
            raise ValueError(
                "logistic-regression training requires at least one example"
            )

        if len(training_features) != len(good_plan_labels):
            raise ValueError(
                "training features and labels must contain the same number "
                "of examples"
            )

        invalid_feature_rows = [
            row_index
            for row_index, feature_row in enumerate(training_features)
            if len(feature_row) != len(FEATURE_NAMES)
        ]

        if invalid_feature_rows:
            raise ValueError(
                f"every training example must contain {len(FEATURE_NAMES)} "
                f"features; invalid rows: {invalid_feature_rows}"
            )

        if set(good_plan_labels) != {0, 1}:
            raise ValueError("training labels must contain both good and bad plans")

        self.model = SklearnPipeline(
            steps=[
                ("standardize", StandardScaler()),
                (
                    "classify",
                    LogisticRegression(
                        random_state=0,
                        max_iter=1_000,
                    ),
                ),
            ]
        )
        self.model.fit(
            training_features,
            good_plan_labels,
        )

        self.model_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        # TODO(Hriday+Ian): create the final artifact from labeled DEV plans
        # once evaluation labels exist. Held-out TEST labels must remain unseen.
        joblib.dump(
            self.model,
            self.model_path,
        )
        self.fallback_used = False

    def rank(
        self,
        itineraries: list[Itinerary],
        request: TripRequest,
    ) -> list[tuple[Itinerary, PlanCost]]:
        """Rank plans by good-plan probability or use the safe fallback."""

        if self.model is None:
            self.fallback_used = True
            return WeightedRanker(self.tradeoffs_config).rank(
                itineraries,
                request,
            )

        self.fallback_used = False

        if not itineraries:
            return []

        scored_itineraries = [
            (
                itinerary,
                plan_cost(
                    itinerary,
                    request,
                    self.tradeoffs_config,
                ),
            )
            for itinerary in itineraries
        ]
        good_plan_probabilities = self.model.predict_proba(
            [
                extract_feature_values(itinerary, cost)
                for itinerary, cost in scored_itineraries
            ]
        )[:, 1]

        ranked_indexes = sorted(
            range(len(scored_itineraries)),
            key=lambda index: (
                -float(good_plan_probabilities[index]),
                scored_itineraries[index][1].total_min,
                len(scored_itineraries[index][0].stops),
                scored_itineraries[index][0].itinerary_id,
            ),
        )

        return [scored_itineraries[index] for index in ranked_indexes]
