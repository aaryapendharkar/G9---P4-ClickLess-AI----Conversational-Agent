"""Grounding metrics (proposal §V-C). Owner: Ian.

  * provenance completeness (uses clickless.data.validate output)
  * atomic-claim evidence support, FActScore-style (target >= 95 %) over PlanResult.claims
  * data-status mislabeling rate (target 0 %)
  * ontology adequacy: share of the 20 competency questions answerable (Manas supplies queries)
"""

from __future__ import annotations


def claim_support(results, evidence_lookup) -> dict[str, float]:
    raise NotImplementedError("TODO(Ian)")
