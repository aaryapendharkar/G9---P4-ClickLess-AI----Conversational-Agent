"""Provenance helpers: data-status labels shown to the driver (Config C). Owner: Aarya.

Wording proposal (ADR 0010, to confirm with Deepti): "<SOURCE>, <kind> (source updated YYYY-MM-DD)",
plus "; may be out of date" when the source record is older than STALE_AFTER_DAYS at the time
the data was retrieved, or "; source update date unknown" for cached data without one. Ages are
measured against `retrieved_at` (when we copied the fact), not the wall clock, so a frozen
snapshot gives the same labels in every evaluation run.
"""

from __future__ import annotations

from datetime import datetime

from ..contracts import DataStatus, Provenance

STALE_AFTER_DAYS = 365


def is_stale(prov: Provenance, stale_after_days: int = STALE_AFTER_DAYS) -> bool | None:
    """True/False for cached or live facts with an update date; None when it cannot be judged."""
    if prov.status == DataStatus.STATIC or prov.source_updated_at is None:
        return None
    return (prov.retrieved_at - prov.source_updated_at).days > stale_after_days


def status_label(prov: Provenance, now: datetime | None = None) -> str:
    """Short label such as 'AFDC, cached snapshot (source updated 2026-09-30)'.

    `now` is accepted for backward compatibility and ignored (see module docstring).
    """
    kind = {
        DataStatus.STATIC: "reference data",
        DataStatus.CACHED: "cached snapshot",
        DataStatus.RUNTIME: "live",
    }[prov.status]
    updated = f" (source updated {prov.source_updated_at:%Y-%m-%d})" if prov.source_updated_at else ""
    note = ""
    if prov.status != DataStatus.STATIC:
        stale = is_stale(prov)
        if stale is None:
            note = "; source update date unknown"
        elif stale:
            note = "; may be out of date"
    return f"{prov.source}, {kind}{updated}{note}"
