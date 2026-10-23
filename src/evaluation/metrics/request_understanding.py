"""RQ1 metrics (proposal §V-A). Owner: Ian (Deepti does NOT implement these -- no self-grading).

  * per-field precision / recall / F1 against gold_request; complete-request exact match
  * preference-matching precision / recall (TF-IDF vs SBERT on identical labelled matches)
  * clarification-decision F1
  * silent-assumption rate: share of incomplete safety-critical items where the system
    proceeded without clarifying or rejecting (target for C: <= 2 %)
  * priority-phrase accuracy: predicted tradeoff_profile vs gold (TA-feedback category)
"""

from __future__ import annotations


def extraction_prf(items, results) -> dict[str, float]:
    raise NotImplementedError("TODO(Ian): per-field P/R/F1 + exact match")


def silent_assumption_rate(items, results) -> dict[str, float]:
    """Proceeded (status ok/infeasible) on an item with clarification_required=True."""
    flagged = [(i, r) for i, r in zip(items, results, strict=True) if i.clarification_required]
    if not flagged:
        return {"silent_assumption_rate": float("nan"), "n_incomplete": 0}
    silent = sum(r.status in ("ok", "infeasible") for _, r in flagged)
    return {"silent_assumption_rate": silent / len(flagged), "n_incomplete": len(flagged)}
