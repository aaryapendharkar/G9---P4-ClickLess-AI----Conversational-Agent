"""nDCG@k with graded gains: DCG@k = sum_{i=1..k} rel_i / log2(i + 1); NaN when the ideal DCG is 0.

Discounts used below: log2(2) = 1, log2(3) = 1.584963, log2(4) = 2.
"""

import math

import pytest

from evaluation.metrics.planning import dcg_at_k, ndcg_at_k, ndcg_at_k_single

REL = 1e-6


def test_dcg_hand_computed():
    # [3, 2, 3, 0, 1], k = 3 (ranks 4 and 5 are cut off):
    #   3/log2(2) + 2/log2(3) + 3/log2(4)
    # = 3/1       + 2/1.584963 + 3/2
    # = 3         + 1.261860   + 1.5     = 5.761860
    expected = 3 / 1 + 2 / math.log2(3) + 3 / 2
    assert dcg_at_k([3, 2, 3, 0, 1], k=3) == pytest.approx(expected, rel=REL)


def test_ndcg_hand_computed():
    # system DCG@3 = 5.761860 (test above)
    # ideal order of ALL judged labels: [3, 3, 2, 1, 0]
    #   3/log2(2) + 3/log2(3) + 2/log2(4) = 3 + 1.892789 + 1 = 5.892789
    # nDCG@3 = 5.761860 / 5.892789 = 0.977781
    dcg = 3 + 2 / math.log2(3) + 3 / 2
    ideal = 3 + 3 / math.log2(3) + 2 / 2
    assert ndcg_at_k_single([3, 2, 3, 0, 1], k=3) == pytest.approx(dcg / ideal, rel=REL)


def test_ideal_order_is_one_and_k_beyond_length():
    # [3, 2, 1, 0] is already in ideal order, so DCG = ideal DCG and nDCG = 1
    assert ndcg_at_k_single([3, 2, 1, 0], k=3) == pytest.approx(1.0, rel=REL)
    # one judged plan, k = 3 > length: DCG = ideal = 1/log2(2) = 1, nDCG = 1
    assert ndcg_at_k_single([1], k=3) == pytest.approx(1.0, rel=REL)


def test_rank_two_is_discounted():
    # [1, 0]: DCG = 1/log2(2) = 1 = ideal -> nDCG = 1
    # [0, 1]: DCG = 0/log2(2) + 1/log2(3) = 0.630930; ideal [1, 0] = 1 -> nDCG = 0.630930
    # so the list with the relevant plan first scores higher (the J&K b = 2 form gave 1.0 for both)
    assert ndcg_at_k_single([1, 0], k=2) == pytest.approx(1.0, rel=REL)
    assert ndcg_at_k_single([0, 1], k=2) == pytest.approx(1 / math.log2(3), rel=REL)
    assert ndcg_at_k_single([0, 1], k=2) < ndcg_at_k_single([1, 0], k=2)
    # [0, 0, 1]: DCG = 1/log2(4) = 0.5; ideal [1, 0, 0] = 1 -> nDCG = 0.5
    assert ndcg_at_k_single([0, 0, 1], k=3) == pytest.approx(1 / 2, rel=REL)


def test_zero_ideal_is_nan_and_skipped_in_the_mean():
    # all-zero or empty lists have ideal DCG 0 -> NaN
    assert math.isnan(ndcg_at_k_single([0, 0, 0], k=3))
    assert math.isnan(ndcg_at_k_single([], k=3))
    # mean over [3,2,3,0,1] -> 0.977781, [0,0] -> NaN (skipped), [1,0] -> 1
    #   (0.977781 + 1) / 2 = 0.988891
    first = (3 + 2 / math.log2(3) + 3 / 2) / (3 + 3 / math.log2(3) + 2 / 2)
    expected = (first + 1.0) / 2
    assert ndcg_at_k([[3, 2, 3, 0, 1], [0, 0], [1, 0]], k=3) == pytest.approx(expected, rel=REL)
    assert math.isnan(ndcg_at_k([[0], [0, 0]], k=3))
