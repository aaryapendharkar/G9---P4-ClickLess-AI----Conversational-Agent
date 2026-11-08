"""DataStore: the single read path for stations / vehicles / amenities / places.

Reads either data/fixtures (synthetic) or data/snapshot (frozen real data); both share one layout.
Owner: Aarya. Everyone else only calls the public methods below.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

from ..config import repo_path
from ..contracts import Amenity, Place, Provenance, Station, Vehicle


def _phrase_spans(text: str, phrase: str) -> list[tuple[int, int]]:
    """Where `phrase` occurs in `text` as whole words ("i4" matches "my i4", not "i40")."""
    pattern = rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])"
    return [m.span() for m in re.finditer(pattern, text)]


def _covered_chars(spans: list[tuple[int, int]]) -> int:
    """Number of text characters covered by the union of `spans`."""
    covered, end = 0, -1
    for a, b in sorted(spans):
        if b > end:
            covered += b - max(a, end)
            end = b
    return covered


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
        for kind, ids in (("station", [s.station_id for s in stations]), ("vehicle", [v.vehicle_id for v in vehicles])):
            dupes = sorted(i for i, n in Counter(ids).items() if n > 1)
            if dupes:  # a dict would silently keep only the last record (PR #26 review question 1)
                raise ValueError(f"duplicate {kind} ids: {dupes[:10]}")
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
        """`data.source` picks the world: `fixtures` (default in configs) or `snapshot`.

        The environment variable CLICKLESS_DATA_SOURCE=fixtures|snapshot overrides the config for
        one run, so the same config A/B/C can be evaluated on either world. A snapshot is loaded
        only if MANIFEST.json exists and every file still matches its hash (issue #32).
        """
        data = cfg["data"]
        source = os.environ.get("CLICKLESS_DATA_SOURCE", "").strip().lower() or data["source"]
        if source == "fixtures":
            return cls.from_dir(data["fixtures_dir"])
        if source != "snapshot":
            raise ValueError(f"data source must be 'fixtures' or 'snapshot', got {source!r}")
        root = repo_path(data["snapshot_dir"])
        if not (root / "MANIFEST.json").is_file():
            raise FileNotFoundError(
                f"no snapshot in {root}: build it (make snapshot-build) or download it; see data/snapshot/README.md"
            )
        from .snapshot import verify_manifest

        changed = verify_manifest(str(root))
        if changed:
            raise ValueError(f"snapshot in {root} does not match MANIFEST.json (missing or changed: {changed})")
        return cls.from_dir(root)

    # ---------- lookups ----------
    def vehicle(self, vehicle_id: str) -> Vehicle:
        return self.vehicles[vehicle_id]

    def station(self, station_id: str) -> Station:
        return self.stations[station_id]

    def find_vehicles(self, text: str) -> list[Vehicle]:
        """Vehicles whose aliases (whole words) cover the most characters of `text`.

        Each vehicle scores the union of the text spans its aliases match, so the variant that
        explains most of the user's words wins: "Tesla Model 3 RWD" -> RWD row ("tesla model 3" +
        "model 3 rwd" cover 17 chars vs 13 for Long Range). "my Model 3" / "my Tesla Model 3" ->
        both Model 3 rows (tie), which callers report as ambiguous. Returns [] when no alias
        matches. TODO(Deepti/Aarya): model-year disambiguation ("2022 Ioniq 5" when only a
        2024 variant is curated) -- today the year in the text is ignored.
        """
        low = text.lower()
        best: dict[str, int] = {}
        for v in self.vehicles.values():
            spans = [span for a in v.aliases for span in _phrase_spans(low, a)]
            if spans:
                best[v.vehicle_id] = _covered_chars(spans)
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
