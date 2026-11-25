"""Compare TF-IDF amenity-credit policies on the frozen DEV split.

This script is evidence for issue #166. It deliberately has no option for
loading TEST, and it does not alter the live Config A ranking behavior.

Run with snapshot data:

    $env:CLICKLESS_DATA_SOURCE = "snapshot"
    python scripts/analyze_tfidf_credit.py --limit 3
    python scripts/analyze_tfidf_credit.py
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from clickless.config import load_config, repo_path
from clickless.contracts import AmenityPreference, TripRequest
from clickless.pipeline import build_pipeline
from clickless.ranking.tfidf_credit_analysis import (
    compare_policy_costs,
    summarize_policy_results,
)
from evaluation.harness import git_commit
from evaluation.metrics.planning import request_from_item
from evaluation.schema import BenchmarkItem, load_items
from evaluation.snapshot_id import snapshot_info

DEV_ITEMS_PATH = "benchmark/requests/dev"
DEFAULT_OUTPUT_PATH = ".venv/tfidf-credit-policy-dev.json"


def _analysis_request(
    pipeline: Any,
    item: BenchmarkItem,
) -> TripRequest | None:
    """Build a resolved gold request while preserving natural amenity phrases.

    Gold annotations define the fields that should influence planning. The
    parser is used only to recover phrases such as "grab a quick bite", because
    those phrases are the actual input to TF-IDF but are not stored separately
    in the benchmark's structured gold fields.
    """

    gold_request = request_from_item(item)

    # Incomplete requests have no complete planning problem to compare.
    if gold_request is None:
        return None

    parsed_request = pipeline.parser.parse(
        item.text,
        request_id=item.id,
    ).request

    amenities = [
        _preference_with_parsed_phrase(
            gold_preference,
            parsed_request,
        )
        for gold_preference in gold_request.amenities
    ]

    resolved_origin = pipeline.store.geocode(gold_request.origin.text)
    resolved_destination = pipeline.store.geocode(gold_request.destination.text)

    if not resolved_origin.resolved or not resolved_destination.resolved:
        raise ValueError("gold origin or destination is not geocoded")

    return gold_request.model_copy(
        update={
            "origin": resolved_origin,
            "destination": resolved_destination,
            "amenities": amenities,
        }
    )


def _preference_with_parsed_phrase(
    gold_preference: AmenityPreference,
    parsed_request: TripRequest,
) -> AmenityPreference:
    """Copy the matching parser phrase without accepting parser constraints."""

    parsed_match = next(
        (
            parsed_preference
            for parsed_preference in parsed_request.amenities
            if parsed_preference.key == gold_preference.key
        ),
        None,
    )

    # If the parser missed the phrase, the category name remains a deterministic
    # TF-IDF query instead of silently dropping the gold amenity preference.
    phrase = (
        parsed_match.phrase
        if parsed_match is not None and parsed_match.phrase.strip()
        else gold_preference.category.value
    )

    return gold_preference.model_copy(update={"phrase": phrase})


def _candidate_itineraries(
    pipeline: Any,
    request: TripRequest,
) -> list[Any]:
    """Create verified candidate itineraries once for all three policies."""

    vehicle = pipeline.store.vehicle(request.vehicle_id)
    route = pipeline.router.route(
        request.origin,
        request.destination,
    )
    candidates = pipeline.retriever.candidates(route, request)

    reset_detour_state = getattr(
        pipeline.detour_measurer,
        "reset",
        None,
    )
    if reset_detour_state is not None:
        reset_detour_state()

    for candidate in candidates:
        for nearby_amenity in candidate.amenities:
            for preference in request.amenities:
                nearby_amenity.match_scores[preference.key] = pipeline.matcher.score(
                    preference,
                    nearby_amenity.amenity,
                )

        candidate.detour_min_est = pipeline.detour_measurer.detour_min(
            route,
            candidate,
        )

    compatible_candidates, _rejected_candidates = pipeline.filter.apply(
        candidates,
        request,
        vehicle,
    )
    planned_itineraries = pipeline.planner.plan(
        request,
        vehicle,
        route,
        compatible_candidates,
    )

    return [
        itinerary
        for itinerary in planned_itineraries
        if pipeline.verifier.verify(
            itinerary,
            request,
            vehicle,
            route,
        ).ok
    ]


def _analyze_item(
    pipeline: Any,
    item: BenchmarkItem,
    tradeoffs_cfg: dict[str, Any],
) -> dict[str, Any]:
    """Return one benchmark item's comparable policy outcomes."""

    request = _analysis_request(pipeline, item)

    if request is None:
        return {
            "item_id": item.id,
            "category": item.category,
            "status": "skipped_incomplete",
        }

    itineraries = _candidate_itineraries(pipeline, request)

    if not itineraries:
        return {
            "item_id": item.id,
            "category": item.category,
            "status": "no_verified_itinerary",
            "amenities": [
                {
                    "key": preference.key,
                    "phrase": preference.phrase,
                }
                for preference in request.amenities
            ],
        }

    policy_comparison = compare_policy_costs(
        itineraries,
        request,
        tradeoffs_cfg,
    )

    return {
        "item_id": item.id,
        "category": item.category,
        "status": "planned",
        "amenities": [
            {
                "key": preference.key,
                "phrase": preference.phrase,
            }
            for preference in request.amenities
        ],
        "policies": {
            policy: {
                "recommended": asdict(results[0]),
                "candidates": [asdict(result) for result in results],
            }
            for policy, results in policy_comparison.items()
        },
    }


def run_analysis(
    output_path: Path,
    limit: int | None = None,
) -> dict[str, Any]:
    """Run Config A policy comparison over frozen DEV only."""

    config = load_config("A")
    pipeline = build_pipeline(config)
    items = load_items(repo_path(DEV_ITEMS_PATH))

    if limit is not None:
        items = items[:limit]

    item_results: list[dict[str, Any]] = []

    for item in items:
        try:
            item_results.append(
                _analyze_item(
                    pipeline,
                    item,
                    config["tradeoffs"],
                )
            )
        except Exception as error:  # noqa: BLE001 - preserve per-item failures
            item_results.append(
                {
                    "item_id": item.id,
                    "category": item.category,
                    "status": "error",
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            )

    report = {
        "config": "A",
        "split": "dev",
        "items_path": DEV_ITEMS_PATH,
        "limit": limit,
        "run_metadata": {
            **git_commit(),
            **snapshot_info(config),
        },
        "summary": summarize_policy_results(item_results),
        "items": item_results,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
    )

    return report


def main() -> int:
    """Command-line entry point."""

    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(DEFAULT_OUTPUT_PATH),
        help="DEV comparison report path",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="run only the first N DEV items for a smoke check",
    )
    arguments = parser.parse_args()

    report = run_analysis(
        output_path=arguments.out,
        limit=arguments.limit,
    )

    print(json.dumps(report["summary"], indent=2))
    print(f"Report written to {arguments.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
