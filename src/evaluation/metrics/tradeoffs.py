"""Trade-off metrics (TA feedback). Owner: Ian. See docs/TRADEOFFS.md section "Evaluation".

  * pareto_validity: share of presented alternatives that are non-dominated among the ORACLE's
    feasible plans on (time, detour, charge, amenity satisfaction)
  * delta_faithfulness: share of numbers in trade-off sentences that equal the PlanCost /
    Alternative.deltas values (part of grounding; catches LLM "rounding" or invention)
  * weight_sensitivity: Kendall tau of plan rankings across profiles and under +-20 % weight
    perturbation; share of items whose Recommended plan changes
  * charge_time_sensitivity: same, with charge minutes scaled x0.75 / x1.25
"""

from __future__ import annotations


def pareto_validity(items, results, oracle) -> dict[str, float]:
    raise NotImplementedError("TODO(Ian)")


def delta_faithfulness(results) -> dict[str, float]:
    raise NotImplementedError("TODO(Ian): parse numbers from explanation vs Alternative.deltas")


def weight_sensitivity(items, rank_fn, perturbations) -> dict[str, float]:
    raise NotImplementedError("TODO(Ian): Kendall tau + recommendation flip rate")
