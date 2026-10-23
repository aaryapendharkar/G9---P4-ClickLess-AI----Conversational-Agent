"""Benchmark item schema -- one JSON object per line in benchmark/requests/**.jsonl. Owner: Ian.

`gold_request` uses the TripRequest field names (clickless.contracts.request) so extraction
metrics can compare field by field. Keep docs/ANNOTATION_GUIDE.md in sync with this file.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

Author = Literal["deepti", "aarya", "manas", "hriday", "ian"]
Category = Literal[
    "urban", "rural", "sparse_charging", "multi_stop", "incomplete",
    "conflicting_prefs", "accessibility", "priority_tradeoff", "infeasible",
]


class BenchmarkItem(BaseModel):
    id: str
    author: Author
    cross_checked_by: Author | None = None
    category: Category
    route_type: Literal["urban", "rural", "mixed"]
    text: str
    gold_request: dict[str, Any]  # TripRequest fields; omit fields the text does not state
    clarification_required: bool
    missing_safety_fields: list[str] = Field(default_factory=list)
    hard_constraints: dict[str, Any] = Field(default_factory=dict)
    soft_constraints: dict[str, Any] = Field(default_factory=dict)
    double_annotated: bool = False
    notes: str = ""


def load_items(path: str | Path) -> list[BenchmarkItem]:
    path = Path(path)
    if not path.exists():
        return []  # e.g. the test split before the freeze
    files = sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
    items = []
    for f in files:
        for line_no, line in enumerate(f.read_text().splitlines(), 1):
            if line.strip() and not line.lstrip().startswith("//"):
                try:
                    items.append(BenchmarkItem.model_validate(json.loads(line)))
                except Exception as e:  # noqa: BLE001 - report file + line for annotators
                    raise ValueError(f"{f}:{line_no}: {e}") from e
    return items
