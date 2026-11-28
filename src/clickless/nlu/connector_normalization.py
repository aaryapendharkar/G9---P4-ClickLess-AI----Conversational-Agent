"""Normalize driver-facing connector names to the request contract."""

from __future__ import annotations

import re

from ..contracts import Connector

CONNECTOR_ALIASES = {
    "ccs": Connector.CCS1,
    "ccs1": Connector.CCS1,
    "combo 1": Connector.CCS1,
    "ccs combo 1": Connector.CCS1,
    "j1772 combo": Connector.CCS1,
    "nacs": Connector.NACS,
    "tesla": Connector.NACS,
    "chademo": Connector.CHADEMO,
    "j1772": Connector.J1772,
}


def normalize_connector(value: str) -> Connector:
    """Map a common connector name to its canonical enum."""
    normalized_value = re.sub(r"\s+", " ", value.strip().lower())
    try:
        return CONNECTOR_ALIASES[normalized_value]
    except KeyError as error:
        raise ValueError(f"unsupported connector: {value}") from error
