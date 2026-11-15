"""nDCG@k, Jarvelin & Kekalainen (2002): graded gains, log2 discount from rank 2 on, NaN for ideal DCG 0."""

import math

import pytest

from evaluation.metrics.planning import dcg_at_k, ndcg_at_k, ndcg_at_k_single

LOG2_3 = math.log2(3)


def test_dcg_hand_computed():
    # 3 + 2/log2(2) + 3/log2(3): ranks 1 and 2 are undiscounted with b = 2
    assert dcg_at_k([3, 2, 3, 0, 1], k=3) == pytest.approx(3 + 2 + 3 / LOG2_3)


def test_ndcg_hand_computed():
    # ideal from ALL judged labels: [3, 3, 2, 1, 0] -> 3 + 3 + 2/log2(3)
    expected = (5 + 3 / LOG2_3) / (6 + 2 / LOG2_3)
    assert ndcg_at_k_single([3, 2, 3, 0, 1], k=3) == pytest.approx(expected)
    assert expected == pytest.approx(0.949177, abs=1e-6)


def test_ideal_order_is_one_and_k_beyond_length():
    assert ndcg_at_k_single([3, 2, 1, 0], k=3) == 1.0
    assert ndcg_at_k_single([1], k=3) == 1.0


def test_original_jk_form_does_not_discount_rank_two():
    # J&K with b = 2: rank 2 gets 1/log2(2) = 1, so [0, 1] scores like [1, 0]. The later
    # rel/log2(i+1) variant would give 1/log2(3) = 0.63 here.
    assert ndcg_at_k_single([0, 1], k=2) == 1.0
    assert ndcg_at_k_single([0, 0, 1], k=3) == pytest.approx(1 / LOG2_3)


def test_zero_ideal_is_nan_and_skipped_in_the_mean():
    assert math.isnan(ndcg_at_k_single([0, 0, 0], k=3))
    assert math.isnan(ndcg_at_k_single([], k=3))
    expected = (0.949177 + 1.0) / 2
    assert ndcg_at_k([[3, 2, 3, 0, 1], [0, 0], [1, 0]], k=3) == pytest.approx(expected, abs=1e-6)
    assert math.isnan(ndcg_at_k([[0], [0, 0]], k=3))
