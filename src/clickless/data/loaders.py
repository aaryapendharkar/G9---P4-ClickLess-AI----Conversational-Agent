"""DataStore: the single read path for stations / vehicles / amenities / places.

Reads either data/fixtures (synthetic) or data/snapshot (frozen real data); both share one layout.
Owner: Aarya. Everyone else only calls the public methods below.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from ..config import repo_path
from ..contracts import Amenity, Place, Provenance, Station, Vehicle


def _contains_phrase(text: str, phrase: str) -> bool:
    """`phrase` occurs in `text` not glued to other letters/digits ("i4" matches "my i4", not "i40")."""
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text) is not None


def _read_records(path: Path) -> tuple[list[dict[str, Any]] | dict[str, Any], dict[str, Any]]:
    with open(path) as f:
        payload = json.load(f)
    return payload["records"], payload.get("_meta", {})


def _with_default_provenance(rec: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    if "provenance" not in rec and "default_provenance" in meta:
        rec = {**rec, "provenance": meta["default_provenance"]}
    return rec


class DataStore:
    def __init__(
        self,
        stations: list[Station],
        vehicles: list[Vehicle],
        amenities: list[Amenity],
        places: dict[str, tuple[float, float]],
        routes_dir: Path,
    ) -> None:
        self.stations = {s.station_id: s for s in stations}
        self.vehicles = {v.vehicle_id: v for v in vehicles}
        self.amenities = amenities
        self.places = places
        self.routes_dir = routes_dir

    # ---------- construction ----------
    @classmethod
    def from_dir(cls, root: str | Path) -> DataStore:
        root = repo_path(root)
        st, st_meta = _read_records(root / "stations.json")
        ve, ve_meta = _read_records(root / "vehicles.json")
        am, am_meta = _read_records(root / "amenities.json")
        pl, _ = _read_records(root / "places.json")
        stations = [Station.model_validate(_with_default_provenance(r, st_meta)) for r in st]
        vehicles = []
        for r in ve:
            prov = r.get("field_provenance") or {}
            if not prov and "default_provenance" in ve_meta:
                default = Provenance.model_validate(ve_meta["default_provenance"])
                prov = {k: default for k in ("usable_kwh", "efficiency_kwh_per_km", "max_dc_kw")}
            vehicles.append(Vehicle.model_validate({**r, "field_provenance": prov}))
        amenities = [Amenity.model_validate(_with_default_provenance(r, am_meta)) for r in am]
        places = {k.lower(): (v[0], v[1]) for k, v in pl.items()}
        return cls(stations, vehicles, amenities, places, root / "routes")

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> DataStore:
        data = cfg["data"]
        root = data["fixtures_dir"] if data["source"] == "fixtures" else data["snapshot_dir"]
        return cls.from_dir(root)

    # ---------- lookups ----------
    def vehicle(self, vehicle_id: str) -> Vehicle:
        return self.vehicles[vehicle_id]

    def station(self, station_id: str) -> Station:
        return self.stations[station_id]

    def find_vehicles(self, text: str) -> list[Vehicle]:
        """Vehicles whose alias appears in `text` as whole words; the longest matching alias wins.

        "my Model 3 RWD" -> only the RWD row ("model 3 rwd" beats "model 3"); "my Model 3" -> both
        Model 3 rows (equal-length tie), which callers report as ambiguous. Returns [] when no
        alias matches. TODO(Deepti/Aarya): model-year disambiguation ("2022 Ioniq 5" when only a
        2024 variant is curated) -- today the year in the text is ignored.
        """
        low = text.lower()
        best: dict[str, int] = {}
        for v in self.vehicles.values():
            lengths = [len(a) for a in v.aliases if _contains_phrase(low, a)]
            if lengths:
                best[v.vehicle_id] = max(lengths)
        if not best:
            return []
        top = max(best.values())
        return [self.vehicles[vid] for vid, n in best.items() if n == top]

    def geocode(self, text: str) -> Place:
        """Fixture gazetteer lookup. TODO(Aarya): cached geocoder for the real benchmark."""
        key = text.strip().lower()
        if key in self.places:
            lat, lon = self.places[key]
            return Place(text=key.title(), lat=lat, lon=lon)
        return Place(text=text.strip())
