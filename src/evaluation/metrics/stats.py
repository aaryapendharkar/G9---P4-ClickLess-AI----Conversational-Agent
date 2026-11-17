"""Statistics (proposal §V-D). Owner: Ian.

  * 95 % bootstrap CIs (percentile, >= 10k resamples, fixed seed)
  * McNemar's test for paired success rates (A vs B, B vs C, C vs LLM-planner)
  * Wilcoxon signed-rank for paired continuous metrics (scipy.stats.wilcoxon)
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any

import numpy as np

MCNEMAR_EXACT_BELOW = 25  # discordant pairs; below this the chi-square approximation is poor


def bootstrap_ci(
    values: Sequence[Any],
    n: int = 10_000,
    alpha: float = 0.05,
    seed: int = 573,
    statistic: Callable[[list[Any]], float] | None = None,
) -> tuple[float, float]:
    """Percentile bootstrap CI of `statistic` (default: the mean) over resamples of `values`.

    `values` may be any per-item records when a custom `statistic` is given (e.g. per-item
    TP/FP/FN counts -> F1). Resamples on which the statistic is NaN are dropped. Returns
    (nan, nan) for empty input or when every resample is NaN. Deterministic for a fixed seed.
    """
    m = len(values)
    if m == 0:
        return (float("nan"), float("nan"))
    idx = np.random.default_rng(seed).integers(0, m, size=(n, m))
    if statistic is None:
        arr = np.asarray(values, dtype=float)
        stats = arr[idx].mean(axis=1)
    else:
        vals = list(values)
        stats = np.array([statistic([vals[j] for j in row]) for row in idx], dtype=float)
    stats = stats[~np.isnan(stats)]
    if stats.size == 0:
        return (float("nan"), float("nan"))
    lo, hi = np.percentile(stats, [100 * alpha / 2, 100 * (1 - alpha / 2)])
    return (float(lo), float(hi))


def _binom_two_sided(k: int, n: int) -> float:
    """Exact two-sided binomial p-value for k successes in n trials at p = 0.5."""
    tail = sum(math.comb(n, i) for i in range(min(k, n - k) + 1)) / 2**n
    return min(1.0, 2 * tail)


def mcnemar(success_a: list[bool], success_b: list[bool]) -> dict[str, float]:
    """McNemar's test on paired successes.

    b = A succeeds & B fails, c = A fails & B succeeds. With fewer than MCNEMAR_EXACT_BELOW
    discordant pairs the exact two-sided binomial test is used (statistic = min(b, c));
    otherwise the continuity-corrected chi-square, (|b - c| - 1)^2 / (b + c), df = 1.
    No discordant pairs -> p = 1.
    """
    if len(success_a) != len(success_b):
        raise ValueError("paired lists must have equal length")
    b = sum(bool(x) and not bool(y) for x, y in zip(success_a, success_b, strict=True))
    c = sum(bool(y) and not bool(x) for x, y in zip(success_a, success_b, strict=True))
    nd = b + c
    if nd == 0:
        stat, p, exact = 0.0, 1.0, True
    elif nd < MCNEMAR_EXACT_BELOW:
        stat, p, exact = float(min(b, c)), _binom_two_sided(min(b, c), nd), True
    else:
        stat = (abs(b - c) - 1) ** 2 / nd
        p, exact = math.erfc(math.sqrt(stat / 2)), False  # chi-square(1) survival function
    return {"b": b, "c": c, "n_discordant": nd, "statistic": stat, "p_value": p, "exact": exact}


def wilcoxon(x: list[float], y: list[float]) -> dict[str, float]:
    """Wilcoxon signed-rank test (two-sided) on paired values via scipy.stats.wilcoxon.

    Zero differences are dropped (scipy's default "wilcox" method). If every difference is
    zero the test is undefined: statistic and p_value are NaN.
    """
    from scipy.stats import wilcoxon as _wilcoxon  # dev dependency; imported lazily

    if len(x) != len(y):
        raise ValueError("paired lists must have equal length")
    d = np.asarray(x, dtype=float) - np.asarray(y, dtype=float)
    nz = int(np.count_nonzero(d))
    if nz == 0:
        return {"statistic": float("nan"), "p_value": float("nan"), "n": len(d), "n_nonzero": 0}
    res = _wilcoxon(x, y)
    return {"statistic": float(res.statistic), "p_value": float(res.pvalue), "n": len(d), "n_nonzero": nz}
