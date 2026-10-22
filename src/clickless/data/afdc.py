"""AFDC (NREL Alternative Fuel Stations API) ingestion. Owner: Aarya.

Plan:
  1. fetch_stations(): pull public EV stations (all US or per state) -> data/raw/afdc/*.json
  2. to_station(): one raw record -> contracts.Station, with Provenance(source="AFDC",
     source_record_id=<id>, source_updated_at=<record update date>, status=CACHED)
  3. port mapping: per-connector counts and power. AFDC reports DC/L2 port counts per station,
     not always per connector -- document the assumption you make in an ADR.
API key: env NREL_API_KEY (never commit it).
"""

from __future__ import annotations

from typing import Any

from ..contracts import Station


def fetch_stations(api_key: str, state: str | None = None, out_dir: str = "data/raw/afdc") -> str:
    """Download raw station records and return the path written. TODO(Aarya)."""
    raise NotImplementedError("TODO(Aarya): AFDC download with paging + retrieval timestamp")


def to_station(record: dict[str, Any], retrieved_at: str) -> Station:
    """Normalize one raw AFDC record (uses data.connectors). TODO(Aarya)."""
    raise NotImplementedError("TODO(Aarya): AFDC record -> Station")
