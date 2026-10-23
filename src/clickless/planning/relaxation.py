"""Minimum-cost relaxation when no plan is feasible (proposal §I item 5). Owner: Hriday.

Search over relaxation steps from configs/relaxation.yaml in priority order (amenities first,
then detour, then arrival SoC; NEVER connector compatibility or physical reachability), re-run
plan+verify for each candidate relaxation, and return the cheapest feasible one with its
alternatives. Ian's oracle independently checks that the returned relaxation is minimum-cost.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..contracts import Alternative, Relaxation, TripRequest


class MinCostRelaxer:
    def __init__(self, relaxation_cfg: dict[str, Any]) -> None:
        self.cfg = relaxation_cfg

    def find(self, request: TripRequest, solve: Callable[[TripRequest], list[Alternative]]) -> Relaxation | None:
        raise NotImplementedError("TODO(Hriday): minimum-cost relaxation search")
