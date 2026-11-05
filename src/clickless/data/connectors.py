"""Connector normalization: source-specific connector codes -> contracts.Connector.

Owner: Aarya. Used by AFDC ingestion (and OSM socket tags if we ever use them).
"""

from __future__ import annotations

from ..contracts import Connector

# AFDC `ev_connector_types` codes -> our enum. Every value listed by the All Stations API docs
# (https://developer.nrel.gov/docs/transportation/alt-fuel-stations-v1/all/, checked 2026-11-03)
# is mapped here; decisions recorded in docs/decisions/0010-afdc-station-normalization.md.
#   * NEMA 14-50 / 5-15 / 5-20 are household outlets (Level 1/2 with the car's own cord): dropped.
#   * J3271 is MCS (Megawatt Charging System) for heavy trucks; no curated vehicle uses it: dropped.
AFDC_CONNECTOR_MAP: dict[str, Connector | None] = {
    "J1772": Connector.J1772,
    "J1772COMBO": Connector.CCS1,
    "CHADEMO": Connector.CHADEMO,
    "TESLA": Connector.NACS,  # AFDC: "NACS (J3400 AC/DC)"
    "NEMA1450": None,
    "NEMA515": None,
    "NEMA520": None,
    "J3271": None,
}


def normalize_afdc_connector(code: str) -> Connector | None:
    """Return the normalized connector, or None if we deliberately ignore it.

    Raises KeyError for an unknown code so new codes are noticed rather than silently dropped.
    """
    return AFDC_CONNECTOR_MAP[code.strip().upper()]
