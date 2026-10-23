"""Planning-quality metrics (proposal §V-B). Owner: Ian.

  * plan feasibility rate (oracle-feasible items that got an oracle-valid itinerary; target >= 90 %)
  * hard-constraint violation rate over returned itineraries (target: zero observed)
  * infeasible items: correct rejection, relaxation feasible + minimum-cost
  * nDCG@3 against graded relevance labels (30 test items, 2 annotators)
  * robust feasibility under +15 % consumption
"""

from __future__ import annotations


def feasibility_and_violations(items, results, oracle) -> dict[str, float]:
    raise NotImplementedError("TODO(Ian): uses LabelSettingOracle.is_feasible / is_valid")


def ndcg_at_k(ranked_relevances: list[list[float]], k: int = 3) -> float:
    raise NotImplementedError("TODO(Ian): nDCG@k (Jarvelin & Kekalainen)")
