"""Connector normalization: source-specific connector codes -> contracts.Connector.

Owner: Aarya. Used by AFDC ingestion (and OSM socket tags if we ever use them).
"""

from __future__ import annotations

from ..contracts import Connector

# AFDC `ev_connector_types` codes -> our enum.
# TODO(Aarya): verify every code against the AFDC API docs and record the doc URL in an ADR.
#   Decide explicitly what to do with NEMA (L1 household) codes -- currently dropped (None).
AFDC_CONNECTOR_MAP: dict[str, Connector | None] = {
    "J1772": Connector.J1772,
    "J1772COMBO": Connector.CCS1,
    "CHADEMO": Connector.CHADEMO,
    "TESLA": Connector.NACS,
    "NEMA1450": None,
    "NEMA515": None,
    "NEMA520": None,
}


def normalize_afdc_connector(code: str) -> Connector | None:
    """Return the normalized connector, or None if we deliberately ignore it.

    Raises KeyError for an unknown code so new codes are noticed rather than silently dropped.
    """
    return AFDC_CONNECTOR_MAP[code.strip().upper()]
