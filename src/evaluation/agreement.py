"""Inter-annotator agreement on the 40 double-annotated items (proposal §II). Owner: Ian.

  * Cohen's kappa for categorical decisions (clarification_required, category)
  * exact agreement for structured fields; tolerance agreement for numeric ones (e.g. +-2 %)
"""

from __future__ import annotations

from collections import Counter

from .metrics.request_understanding import normalize_field


def cohen_kappa(labels_a: list, labels_b: list) -> float:
    """Cohen's kappa = (p_o - p_e) / (1 - p_e).

    NaN when undefined: empty input, or p_e == 1 (both annotators used one identical label).
    """
    if len(labels_a) != len(labels_b):
        raise ValueError("label lists must have equal length")
    n = len(labels_a)
    if n == 0:
        return float("nan")
    po = sum(a == b for a, b in zip(labels_a, labels_b, strict=True)) / n
    ca, cb = Counter(labels_a), Counter(labels_b)
    pe = sum(ca[k] * cb[k] for k in ca) / n**2
    if pe == 1:
        return float("nan")
    return (po - pe) / (1 - pe)


def field_agreement(golds_a: list[dict], golds_b: list[dict], tolerances: dict[str, float]) -> dict[str, float]:
    """Per-field agreement between two annotators' gold_request dicts (paired by position).

    Fields = union of keys used by either annotator. Values are compared after the same
    normalization as the extraction metrics (normalize_field: place text case/punctuation-
    insensitive, amenities and connectors as sets, ...); a field both annotators omitted agrees.

    "<field>.exact": share of items with equal values.
    "<field>.tolerance" (only for fields in `tolerances`): share of items where both values are
    absent, or both present and |a - b| <= tolerance (absolute, in the field's own units, e.g.
    2 percentage points for start_soc_pct).
    """
    if len(golds_a) != len(golds_b):
        raise ValueError("gold lists must have equal length")
    n = len(golds_a)
    out: dict[str, float] = {"n": n}
    if n == 0:
        return out
    fields = sorted({k for g in golds_a + golds_b for k in g})
    for f in fields:
        pairs = [
            (normalize_field(f, a.get(f)), normalize_field(f, b.get(f)))
            for a, b in zip(golds_a, golds_b, strict=True)
        ]
        out[f"{f}.exact"] = sum(x == y for x, y in pairs) / n
        if f in tolerances:
            tol = tolerances[f]
            ok = sum(
                (x is None and y is None) or (x is not None and y is not None and abs(float(x) - float(y)) <= tol)
                for x, y in pairs
            )
            out[f"{f}.tolerance"] = ok / n
    return out
