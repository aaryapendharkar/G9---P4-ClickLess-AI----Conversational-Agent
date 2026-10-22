"""Provenance helpers: data-status labels shown to the driver (Config C). Owner: Aarya."""

from __future__ import annotations

from datetime import datetime

from ..contracts import DataStatus, Provenance


def status_label(prov: Provenance, now: datetime | None = None) -> str:
    """Short label such as 'AFDC, cached snapshot (source updated 2026-09-30)'.

    TODO(Aarya): age warnings (e.g. 'status may be stale') and wording agreed with Deepti.
    """
    kind = {
        DataStatus.STATIC: "reference data",
        DataStatus.CACHED: "cached snapshot",
        DataStatus.RUNTIME: "live",
    }[prov.status]
    updated = f" (source updated {prov.source_updated_at:%Y-%m-%d})" if prov.source_updated_at else ""
    return f"{prov.source}, {kind}{updated}"
