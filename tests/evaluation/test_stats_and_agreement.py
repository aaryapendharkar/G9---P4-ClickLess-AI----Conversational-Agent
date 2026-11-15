"""Hand-computed examples for evaluation.metrics.stats and evaluation.agreement."""

import math

import pytest

from evaluation.agreement import cohen_kappa, field_agreement
from evaluation.metrics.stats import bootstrap_ci, mcnemar, wilcoxon

# --------------------------------------------------------------------------- bootstrap


def test_bootstrap_defaults_to_10k_resamples():
    import inspect

    assert inspect.signature(bootstrap_ci).parameters["n"].default == 10_000


def test_bootstrap_constant_and_empty():
    assert bootstrap_ci([0.7] * 10) == (0.7, 0.7)
    assert all(math.isnan(x) for x in bootstrap_ci([]))


def test_bootstrap_is_deterministic_and_seeded():
    vals = [0.13, 0.57, 0.92, 0.31, 0.78, 0.05, 0.66]
    assert bootstrap_ci(vals) == bootstrap_ci(vals)
    assert bootstrap_ci(vals, seed=1) != bootstrap_ci(vals, seed=2)


def test_bootstrap_two_points_spans_full_range():
    # Resamples of {0, 1} with m=2 have mean 0 (p=1/4), 0.5 (1/2) or 1 (1/4): the 2.5th and
    # 97.5th percentiles are 0 and 1.
    assert bootstrap_ci([0, 1]) == (0.0, 1.0)


def test_bootstrap_custom_statistic_and_nan_resamples():
    # max over a resample of [1, 2, 3]: P(max = 1) = 1/27 (3.7 %) > 2.5 %, so lo = 1; hi = 3.
    assert bootstrap_ci([1, 2, 3], statistic=max) == (1.0, 3.0)
    # NaN resamples are dropped: the statistic is NaN unless the resample contains a 2.
    stat = lambda s: 2.0 if 2 in s else float("nan")
    assert bootstrap_ci([1, 2], statistic=stat) == (2.0, 2.0)


# --------------------------------------------------------------------------- McNemar


def _paired(b: int, c: int, both: int = 3, neither: int = 2):
    a = [True] * b + [False] * c + [True] * both + [False] * neither
    bb = [False] * b + [True] * c + [True] * both + [False] * neither
    return a, bb


def test_mcnemar_exact_small_counts():
    # b=5, c=1, n=6: p = 2 * P(X <= 1 | Bin(6, .5)) = 2 * (1 + 6) / 64
    m = mcnemar(*_paired(5, 1))
    assert (m["b"], m["c"], m["n_discordant"], m["exact"]) == (5, 1, 6, True)
    assert m["p_value"] == pytest.approx(14 / 64)
    # b=c=3: 2 * P(X <= 3 | Bin(6, .5)) = 84/64, capped at 1
    assert mcnemar(*_paired(3, 3))["p_value"] == 1.0


def test_mcnemar_no_discordant_pairs():
    m = mcnemar([True, False], [True, False])
    assert (m["n_discordant"], m["p_value"]) == (0, 1.0)


def test_mcnemar_chi_square_large_counts():
    from scipy.stats import chi2

    # b=30, c=10: (|30 - 10| - 1)^2 / 40 = 9.025
    m = mcnemar(*_paired(30, 10))
    assert m["exact"] is False and m["statistic"] == pytest.approx(9.025)
    assert m["p_value"] == pytest.approx(chi2.sf(9.025, 1))


def test_mcnemar_length_mismatch():
    with pytest.raises(ValueError):
        mcnemar([True], [True, False])


# --------------------------------------------------------------------------- Wilcoxon


def test_wilcoxon_all_positive_differences():
    # differences 1..5 all positive: W- = 0, exact two-sided p = 2 / 2^5
    m = wilcoxon([2, 4, 6, 8, 10], [1, 2, 3, 4, 5])
    assert m["statistic"] == 0.0 and m["p_value"] == pytest.approx(2 / 32)
    assert (m["n"], m["n_nonzero"]) == (5, 5)


def test_wilcoxon_all_zero_is_undefined():
    m = wilcoxon([1, 2], [1, 2])
    assert math.isnan(m["p_value"]) and m["n_nonzero"] == 0


# --------------------------------------------------------------------------- agreement


def test_cohen_kappa_hand_computed():
    # p_o = 3/4; p_e = .5 * .25 + .5 * .75 = .5; kappa = (.75 - .5) / .5 = .5
    assert cohen_kappa([1, 1, 0, 0], [1, 0, 0, 0]) == pytest.approx(0.5)
    assert cohen_kappa(["a", "b", "c"], ["a", "b", "c"]) == 1.0
    # complete disagreement on a balanced binary task: p_o = 0, p_e = .5 -> -1
    assert cohen_kappa([True, False], [False, True]) == -1.0


def test_cohen_kappa_matches_sklearn():
    from sklearn.metrics import cohen_kappa_score

    a = ["urban", "rural", "rural", "incomplete", "urban", "urban", "rural"]
    b = ["urban", "rural", "urban", "incomplete", "urban", "rural", "rural"]
    assert cohen_kappa(a, b) == pytest.approx(cohen_kappa_score(a, b))


def test_cohen_kappa_undefined():
    assert math.isnan(cohen_kappa([True, True], [True, True]))  # p_e = 1
    assert math.isnan(cohen_kappa([], []))


def test_field_agreement_hand_computed():
    a = [{"origin": "Phoenix, AZ", "start_soc_pct": 65},
         {"origin": "Tempe", "start_soc_pct": 40, "amenities": [{"category": "food"}]}]
    b = [{"origin": "phoenix, arizona", "start_soc_pct": 66},
         {"origin": "Mesa", "start_soc_pct": 40, "amenities": [{"category": "food", "priority": "nice_to_have"}]}]
    m = field_agreement(a, b, {"start_soc_pct": 2})
    assert m["n"] == 2
    assert m["origin.exact"] == 0.5
    assert m["start_soc_pct.exact"] == 0.5 and m["start_soc_pct.tolerance"] == 1.0
    assert m["amenities.exact"] == 1.0  # absent/absent, then equal after contract defaults
    assert "origin.tolerance" not in m
    # outside tolerance, and present vs absent never agrees
    m2 = field_agreement([{"start_soc_pct": 65}, {"start_soc_pct": 10}], [{"start_soc_pct": 68}, {}],
                         {"start_soc_pct": 2})
    assert m2["start_soc_pct.tolerance"] == 0.0
