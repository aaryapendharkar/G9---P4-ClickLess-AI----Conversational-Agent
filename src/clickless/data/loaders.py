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

_NEGATION = re.compile(r"\b(not|neither|nor|isn'?t|aren'?t|don'?t|doesn'?t)\b|n't\b")
_LABEL_WORD = re.compile(r"[a-z0-9.]+")


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
    with open(path, encoding="utf-8") as f:
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

    def vehicle_label(self, vehicle_id: str) -> str:
        """Driver-facing name of a vehicle, e.g. "2024 Tesla Model 3 RWD".

        Use it to *show* vehicle options in a clarification question; keep the vehicle_id itself
        as the machine value in `ParseResult.ambiguous_fields` (ADR 0014). Unknown ids raise
        KeyError, like `vehicle()`.
        """
        v = self.vehicles[vehicle_id]
        return " ".join(str(part) for part in (v.model_year, v.make, v.model, v.variant) if part)

    def find_vehicles(self, text: str, among: list[str] | None = None) -> list[Vehicle]:
        """Vehicles whose aliases (whole words) cover the most characters of `text`.

        Each vehicle scores the union of the text spans its aliases match, so the variant that
        explains most of the user's words wins: "Tesla Model 3 RWD" -> RWD row ("tesla model 3" +
        "model 3 rwd" cover 17 chars vs 13 for Long Range). "my Model 3" / "my Tesla Model 3" ->
        both Model 3 rows (tie), which callers report as ambiguous. Returns [] when no alias
        matches.

        `among` (optional) limits the search to these vehicle_ids. A clarification answer passes
        the ids it offered as options, so the reply can only pick one of them, never a different
        car (ADR 0014). Ids not in the store are ignored. When aliases do not single out one of
        the offered vehicles, the words that tell the offered labels apart are tried next, so short
        replies such as "the RWD one" or "long range" pick the one option they name; words shared
        by several options ("model 3") or naming several options at once resolve nothing.

        TODO(Deepti/Aarya): model-year disambiguation ("2022 Ioniq 5" when only a 2024 variant is
        curated) -- today the year in the text is ignored.
        """
        low = text.lower()
        if among is not None and _NEGATION.search(low):
            return []  # "not the RWD one": safer to ask again than to pick by the words left over
        if among is None:
            pool = list(self.vehicles.values())
        else:
            pool = [self.vehicles[i] for i in among if i in self.vehicles]
        best: dict[str, int] = {}
        for v in pool:
            spans = [span for a in v.aliases for span in _phrase_spans(low, a)]
            if spans:
                best[v.vehicle_id] = _covered_chars(spans)
        top = max(best.values(), default=0)
        found = [self.vehicles[vid] for vid, n in best.items() if n == top]
        if among is not None and len(found) != 1:
            picked = self._pick_by_distinguishing_words(low, found or pool)
            if picked is not None:
                return [picked]
        return found

    def _pick_by_distinguishing_words(self, low: str, options: list[Vehicle]) -> Vehicle | None:
        """The one option whose label words, unique among `options`, appear in `low`; else None."""
        # e.g. Model 3 LR AWD -> {"2024", "tesla", "model", "3", "long", "range", "awd"}; RWD -> {..., "rwd"}
        words = {v.vehicle_id: set(_LABEL_WORD.findall(self.vehicle_label(v.vehicle_id).lower())) for v in options}
        counts = Counter(w for ws in words.values() for w in ws)
        hits = {
            vid
            for vid, ws in words.items()
            for w in ws
            if counts[w] == 1 and _phrase_spans(low, w)
        }
        return self.vehicles[hits.pop()] if len(hits) == 1 else None

    def geocode(self, text: str) -> Place:
        """Offline gazetteer lookup in `places.json` (fixtures, or the snapshot built by geocode.py).

        Never calls the network and never guesses: a name that is not in the gazetteer, or a bare
        ambiguous name such as "Springfield", comes back unresolved (`lat`/`lon` None), and the
        caller must ask the driver again. The snapshot gazetteer is closed: it holds the benchmark
        origins/destinations ("City, ST" and the bare city when unique), so a clarification answer
        naming another place stays unresolved (ADR 0014).
        """
        key = text.strip().lower()
        if key in self.places:
            lat, lon = self.places[key]
            return Place(text=key.title(), lat=lat, lon=lon)
        return Place(text=text.strip())
