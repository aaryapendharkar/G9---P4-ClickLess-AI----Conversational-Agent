"""nDCG@k with graded gains: DCG@k = sum_{i=1..k} rel_i / log2(i + 1); NaN when the ideal DCG is 0.

Discounts used below: log2(2) = 1, log2(3) = 1.584963, log2(4) = 2.
"""

import math

import pytest

from evaluation.metrics.planning import dcg_at_k, ndcg_at_k, ndcg_at_k_single, plan_key, pooled_ndcg_at_k

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


# --------------------------------------------------------------------------- pooled ideal (#184)


def test_ideal_comes_from_the_pool_not_the_configs_own_list():
    # The config returned three marginal plans (grade 1); another config found a grade-3 plan.
    # Against its own list it would score 1.0. Against the pool [3, 1, 1, 1]:
    #   DCG   = 1/1 + 1/1.584963 + 1/2           = 2.130930
    #   ideal = 3/1 + 1/1.584963 + 1/2           = 4.130930
    assert ndcg_at_k_single([1, 1, 1], k=3) == pytest.approx(1.0, rel=REL)
    expected = (1 + 1 / math.log2(3) + 1 / 2) / (3 + 1 / math.log2(3) + 1 / 2)
    assert ndcg_at_k_single([1, 1, 1], k=3, pool=[3, 1, 1, 1]) == pytest.approx(expected, rel=REL)


def test_ndcg_at_k_takes_one_pool_per_list():
    # list 1: DCG 2, ideal over [3, 2] = 3 + 2/log2(3); list 2: 3 / 3 = 1
    expected = (2 / (3 + 2 / math.log2(3)) + 1) / 2
    assert ndcg_at_k([[2], [3]], k=3, pools=[[3, 2], [3]]) == pytest.approx(expected, rel=REL)
    assert math.isnan(ndcg_at_k([[0]], k=3, pools=[[0, 0]]))
    with pytest.raises(ValueError):
        ndcg_at_k([[1], [1]], k=3, pools=[[1]])


def test_pooled_ndcg_uses_ordered_station_ids_across_configs():
    a, b, c = ("s1",), ("s2", "s3"), ("s3", "s2")  # b and c: same stations, different order
    grades = {"t-001": {a: 3.0, b: 1.5, c: 0.5}, "t-002": {a: 2.0}}
    ranked = {"t-001": [b, a], "t-002": [a]}
    # t-001: DCG = 1.5 + 3/log2(3); ideal over the pool [3, 1.5, 0.5] = 3 + 1.5/log2(3) + 0.5/2
    t1 = (1.5 + 3 / math.log2(3)) / (3 + 1.5 / math.log2(3) + 0.5 / 2)
    assert pooled_ndcg_at_k(ranked, grades) == pytest.approx((t1 + 1.0) / 2, rel=REL)
    assert plan_key({"station_ids": ["s2", "s3"]}) == b
    assert plan_key({"station_ids": []}) == ()  # a direct, no-stop plan


def test_pooled_ndcg_unpooled_plan_is_0_and_a_missing_item_stays_in_the_denominator():
    grades = {"t-001": {("s1",): 3.0}, "t-002": {("s1",): 2.0}}
    # t-001: an unjudged plan first (gain 0), then the judged one: (3/log2(3)) / 3.
    # t-002: the config returned nothing for a judged item -> 0, not skipped.
    ranked = {"t-001": [("s9",), ("s1",)], "t-999": [("s1",)]}
    assert pooled_ndcg_at_k(ranked, grades) == pytest.approx((1 / math.log2(3) + 0) / 2, rel=REL)
