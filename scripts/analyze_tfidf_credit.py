"""Compare TF-IDF amenity-credit policies on the frozen DEV split.

This script is evidence for issue #166. It deliberately has no option for
loading TEST, and it does not alter Config A configuration or ranking behavior.

Run with snapshot data in PowerShell:

    $env:CLICKLESS_DATA_SOURCE = "snapshot"
    python scripts/analyze_tfidf_credit.py --limit 3

Run with snapshot data in Bash:

    CLICKLESS_DATA_SOURCE=snapshot \
        python scripts/analyze_tfidf_credit.py --limit 3
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from clickless.config import load_config
from clickless.pipeline import build_pipeline
from clickless.ranking.tfidf_credit_analysis import (
    PreparedAnalysisRequest,
    compare_policy_costs,
    matcher_score_diagnostics,
    prepare_analysis_request,
    summarize_policy_results,
    verified_candidate_itineraries,
)
from evaluation.harness import git_commit, select_items
from evaluation.metrics.planning import request_from_item
from evaluation.schema import BenchmarkItem
from evaluation.snapshot_id import snapshot_info

DEV_ITEMS_PATH = "benchmark/requests/dev"
DEFAULT_OUTPUT_PATH = "results/preliminary/tfidf_credit_policy/dev.json"


def _analysis_request(
    pipeline: Any,
    item: BenchmarkItem,
) -> PreparedAnalysisRequest | None:
    """Build a resolved gold request with auditable amenity phrases."""

    gold_request = request_from_item(item)

    # Incomplete requests have no complete planning problem to compare.
    if gold_request is None:
        return None

    parsed_request = pipeline.parser.parse(
        item.text,
        request_id=item.id,
    ).request

    return prepare_analysis_request(
        gold_request,
        parsed_request,
        pipeline.store.geocode,
    )


def _analyze_item(
    pipeline: Any,
    item: BenchmarkItem,
    tradeoffs_cfg: dict[str, Any],
    floor_policy_config: dict[str, Any],
) -> dict[str, Any]:
    """Return one benchmark item's comparable policy outcomes."""

    prepared_request = _analysis_request(pipeline, item)

    if prepared_request is None:
        return {
            "item_id": item.id,
            "category": item.category,
            "status": "skipped_incomplete",
        }

    request = prepared_request.request
    amenity_records = [asdict(amenity) for amenity in prepared_request.amenities]

    itineraries = verified_candidate_itineraries(
        pipeline,
        request,
    )

    if not itineraries:
        return {
            "item_id": item.id,
            "category": item.category,
            "status": "no_verified_itinerary",
            "amenities": amenity_records,
        }

    policy_comparison = compare_policy_costs(
        itineraries,
        request,
        tradeoffs_cfg,
        floor_policy_config=floor_policy_config,
    )

    return {
        "item_id": item.id,
        "category": item.category,
        "status": "planned",
        "amenities": amenity_records,
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

    # Benchmark DEV requests require the frozen real-data snapshot. Using the
    # shared selector prevents a forgotten environment setting from producing
    # a meaningless report against the small fixture world.
    items, _excluded_item_ids = select_items(
        config,
        split="dev",
        items_paths=None,
    )

    pipeline = build_pipeline(config)

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
                    config["ranking"]["tfidf_credit"],
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

    matcher_diagnostics = matcher_score_diagnostics(
        pipeline.matcher,
        pipeline.store.amenities,
    )

    report = {
        "config": "A",
        "split": "dev",
        "items_path": DEV_ITEMS_PATH,
        "limit": limit,
        "tfidf_credit_config": dict(
            config["ranking"]["tfidf_credit"]
        ),
        "run_metadata": {
            **git_commit(),
            **snapshot_info(config),
        },
        "matcher_diagnostics": matcher_diagnostics,
        "summary": summarize_policy_results(item_results),
        "items": item_results,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
    )

    return report


def main(argv: list[str] | None = None) -> int:
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
    arguments = parser.parse_args(argv)

    report = run_analysis(
        output_path=arguments.out,
        limit=arguments.limit,
    )

    print(json.dumps(report["summary"], indent=2))
    print(f"Report written to {arguments.out}")

    error_count = report["summary"]["n_errors"]

    if error_count:
        print(
            f"Analysis completed with {error_count} error(s); see the report.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
