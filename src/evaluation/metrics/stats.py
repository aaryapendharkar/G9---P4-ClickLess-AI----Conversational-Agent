"""Statistics (proposal §V-D). Owner: Ian.

  * 95 % bootstrap CIs (percentile, >= 10k resamples, fixed seed)
  * McNemar's test for paired success rates (A vs B, B vs C, C vs LLM-planner)
  * Wilcoxon signed-rank for paired continuous metrics (scipy.stats.wilcoxon)
"""

from __future__ import annotations


def bootstrap_ci(values: list[float], n: int = 10_000, alpha: float = 0.05, seed: int = 573) -> tuple[float, float]:
    raise NotImplementedError("TODO(Ian)")


def mcnemar(success_a: list[bool], success_b: list[bool]) -> dict[str, float]:
    raise NotImplementedError("TODO(Ian): exact binomial version for small discordant counts")
