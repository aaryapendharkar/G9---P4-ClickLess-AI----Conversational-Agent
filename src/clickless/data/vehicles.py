"""Curated vehicle table loader: data/curated/vehicles.csv -> list[Vehicle]. Owner: Aarya.

Each curated numeric value has its own source URL column; build one Provenance per field
(source="MANUFACTURER" or "FUELECONOMY", status=STATIC, retrieved_at=accessed_on).
"""

from __future__ import annotations

from ..contracts import Vehicle


def load_curated_vehicles(csv_path: str = "data/curated/vehicles.csv") -> list[Vehicle]:
    raise NotImplementedError("TODO(Aarya): CSV -> Vehicle with field_provenance per value")
