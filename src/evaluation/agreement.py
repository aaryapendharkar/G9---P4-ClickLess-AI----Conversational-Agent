"""Inter-annotator agreement on the 40 double-annotated items (proposal §II). Owner: Ian.

  * Cohen's kappa for categorical decisions (clarification_required, category)
  * exact agreement for structured fields; tolerance agreement for numeric ones (e.g. +-2 %)
"""

from __future__ import annotations


def cohen_kappa(labels_a: list, labels_b: list) -> float:
    raise NotImplementedError("TODO(Ian): Cohen's kappa (sklearn.metrics.cohen_kappa_score is fine)")


def field_agreement(golds_a: list[dict], golds_b: list[dict], tolerances: dict[str, float]) -> dict[str, float]:
    raise NotImplementedError("TODO(Ian): per-field exact / tolerance agreement")
