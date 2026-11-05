"""OpenStreetMap amenities via Overpass. Owner: Aarya. Decisions: docs/decisions/0011-osm-amenity-mapping.md.

  1. build_overpass_query(stations, radius_m): one batched query, an `around` clause per station
     (a multi-point `around` would be a polyline, so points are never combined);
  2. fetch_amenities(): batches -> data/raw/osm/*.json (cached per batch, so reruns resume;
     editor names/ids are stripped before saving);
  3. to_amenity() / amenities_from_elements(): element -> contracts.Amenity.

Accessibility (proposal §II): a missing wheelchair tag is "unknown", never accessible. A venue
tagged `toilets=yes` also yields a separate RESTROOM amenity whose accessibility comes from
`toilets:wheelchair` (the venue's own `wheelchair` tag describes its entrance, not its restroom).
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import repo_path
from ..contracts import Amenity, AmenityCategory, DataStatus, Provenance, Station

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "ClickLessCharge/0.1 (ASU CSE 573 course project)"

# tag key -> {tag value -> category}. Checked in this order; the first match wins.
CATEGORY_TAGS: tuple[tuple[str, dict[str, AmenityCategory]], ...] = (
    (
        "amenity",
        {
            "restaurant": AmenityCategory.FOOD,
            "fast_food": AmenityCategory.FOOD,
            "food_court": AmenityCategory.FOOD,
            "cafe": AmenityCategory.COFFEE,
            "toilets": AmenityCategory.RESTROOM,
        },
    ),
    (
        "shop",
        {
            "coffee": AmenityCategory.COFFEE,
            "supermarket": AmenityCategory.GROCERY,
            "convenience": AmenityCategory.GROCERY,
            "greengrocer": AmenityCategory.GROCERY,
        },
    ),
    ("tourism", {"hotel": AmenityCategory.LODGING, "motel": AmenityCategory.LODGING}),
)
_WHEELCHAIR = {"yes": "yes", "designated": "yes", "limited": "limited", "no": "no"}
_KEPT_TAGS = (
    "amenity", "shop", "tourism", "cuisine", "brand", "name", "opening_hours",
    "wheelchair", "toilets", "toilets:wheelchair", "toilets:access", "access",
)  # fmt: skip


class OverpassError(RuntimeError):
    pass


def _key_regex() -> str:
    return "^(" + "|".join(k for k, _ in CATEGORY_TAGS) + ")$"


def _value_regex() -> str:
    values = sorted({v for _, m in CATEGORY_TAGS for v in m})
    return "^(" + "|".join(values) + ")$"


def build_overpass_query(stations: list[Station], radius_m: int = 400, timeout_s: int = 180) -> str:
    if not stations:
        raise ValueError("no stations to query around")
    if radius_m <= 0:
        raise ValueError("radius_m must be positive")
    kv = f'[~"{_key_regex()}"~"{_value_regex()}"]'
    lines = [f"[out:json][timeout:{timeout_s}];", "("]
    for s in stations:
        around = f"around:{radius_m},{s.lat:.6f},{s.lon:.6f}"
        lines.append(f"  nwr({around}){kv};")
        lines.append(f'  nwr({around})["toilets"="yes"];')
    lines += [");", "out meta center;"]
    return "\n".join(lines)


def normalize_category(tags: dict[str, str]) -> AmenityCategory:
    """OSM tags -> AmenityCategory (unknown => OTHER). Category list agreed with the parser enum."""
    for key, mapping in CATEGORY_TAGS:
        value = tags.get(key)
        if value in mapping:
            return mapping[value]
    return AmenityCategory.OTHER


def _wheelchair(value: str | None) -> str:
    return _WHEELCHAIR.get((value or "").strip().lower(), "unknown")


def _coords(element: dict[str, Any]) -> tuple[float, float]:
    if "lat" in element and "lon" in element:
        return float(element["lat"]), float(element["lon"])
    center = element.get("center") or {}
    if "lat" in center and "lon" in center:
        return float(center["lat"]), float(center["lon"])
    raise ValueError(f"OSM {element.get('type')}/{element.get('id')} has no coordinates")


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _provenance(element: dict[str, Any], retrieved_at: datetime) -> Provenance:
    etype, eid = element["type"], element["id"]
    return Provenance(
        source="OSM",
        source_record_id=f"{etype}/{eid}",
        source_url=f"https://www.openstreetmap.org/{etype}/{eid}",
        retrieved_at=retrieved_at,
        source_updated_at=_parse_ts(element.get("timestamp")),
        status=DataStatus.CACHED,
    )


def _as_dt(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    dt = _parse_ts(value)
    if dt is None:
        raise ValueError(f"bad retrieved_at {value!r}")
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def to_amenity(element: dict[str, Any], retrieved_at: str | datetime) -> Amenity:
    """One Overpass element -> Amenity (its category may be OTHER; callers decide to keep it)."""
    tags = element.get("tags") or {}
    lat, lon = _coords(element)
    return Amenity(
        amenity_id=f"osm:{element['type']}/{element['id']}",
        name=tags.get("name"),
        category=normalize_category(tags),
        lat=lat,
        lon=lon,
        wheelchair=_wheelchair(tags.get("wheelchair")),
        opening_hours=tags.get("opening_hours"),
        tags={k: tags[k] for k in _KEPT_TAGS if k in tags},
        provenance=_provenance(element, _as_dt(retrieved_at)),
    )


def _restroom_of(element: dict[str, Any], retrieved_at: datetime) -> Amenity | None:
    tags = element.get("tags") or {}
    if tags.get("toilets") != "yes" or tags.get("amenity") == "toilets":
        return None
    if tags.get("toilets:access") in {"private", "no", "customers_only_staff"}:
        return None
    lat, lon = _coords(element)
    name = tags.get("name")
    return Amenity(
        amenity_id=f"osm:{element['type']}/{element['id']}#toilets",
        name=f"Restroom at {name}" if name else None,
        category=AmenityCategory.RESTROOM,
        lat=lat,
        lon=lon,
        wheelchair=_wheelchair(tags.get("toilets:wheelchair")),
        opening_hours=tags.get("opening_hours"),
        tags={k: tags[k] for k in _KEPT_TAGS if k in tags},
        provenance=_provenance(element, retrieved_at),
    )


def amenities_from_elements(
    elements: list[dict[str, Any]], retrieved_at: str | datetime
) -> tuple[list[Amenity], Counter]:
    """All useful amenities, deduplicated by id; returns (amenities, counts of dropped elements)."""
    when = _as_dt(retrieved_at)
    out: dict[str, Amenity] = {}
    dropped: Counter = Counter()
    for el in elements:
        if el.get("type") not in {"node", "way", "relation"} or "id" not in el:
            dropped["not_an_element"] += 1
            continue
        try:
            main = to_amenity(el, when)
            extra = _restroom_of(el, when)
        except ValueError:
            dropped["no_coordinates"] += 1
            continue
        if main.category != AmenityCategory.OTHER:
            out.setdefault(main.amenity_id, main)
        elif extra is None:
            dropped["other_category"] += 1
        if extra is not None:
            out.setdefault(extra.amenity_id, extra)
    return list(out.values()), dropped


# ---------------------------------------------------------------- download


def _strip_editor_info(element: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in element.items() if k not in {"user", "uid", "changeset"}}


def fetch_amenities(
    stations: list[Station],
    out_dir: str = "data/raw/osm",
    *,
    radius_m: int = 400,
    batch_size: int = 100,
    endpoint: str = OVERPASS_URL,
    pause_s: float = 2.0,
    max_attempts: int = 4,
    progress: bool = True,
) -> list[str]:
    """Query Overpass in batches; each batch file is reused if it already exists (resume)."""
    import requests

    out = repo_path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ordered = sorted(stations, key=lambda s: s.station_id)
    paths: list[str] = []
    n_batches = (len(ordered) + batch_size - 1) // batch_size
    for b in range(n_batches):
        batch = ordered[b * batch_size : (b + 1) * batch_size]
        query = build_overpass_query(batch, radius_m)
        digest = hashlib.sha256(query.encode()).hexdigest()[:16]
        path = out / f"osm_batch_{b:05d}_{digest}.json"
        paths.append(str(path))
        if path.exists():
            continue
        for attempt in range(1, max_attempts + 1):
            retrieved_at = datetime.now(timezone.utc).replace(microsecond=0)
            try:
                resp = requests.post(endpoint, data={"data": query}, headers={"User-Agent": USER_AGENT}, timeout=240)
            except requests.RequestException as e:
                status, err = None, type(e).__name__
            else:
                status, err = resp.status_code, ""
                try:
                    payload = resp.json() if status == 200 else {}
                except ValueError:  # e.g. an HTML error page served with status 200
                    payload, status, err = {}, None, "response was not JSON"
                if status == 200 and payload.get("remark"):
                    err = f"remark: {str(payload['remark'])[:120]}"  # e.g. timeout: incomplete data
                elif status == 200:
                    elements = [_strip_editor_info(e) for e in payload.get("elements", [])]
                    meta = {
                        "retrieved_at": retrieved_at.isoformat(),
                        "endpoint": endpoint,
                        "radius_m": radius_m,
                        "station_ids": [s.station_id for s in batch],
                    }
                    path.write_text(json.dumps({"_meta": meta, "elements": elements}))
                    break
            if attempt == max_attempts:
                raise OverpassError(f"batch {b}: Overpass failed after {attempt} attempts (HTTP {status} {err})")
            time.sleep(pause_s * 2**attempt)  # 429 / 504 / remark / network: back off and retry
        if progress:
            print(f"  OSM batch {b + 1}/{n_batches} done", flush=True)
        time.sleep(pause_s)
    return paths


def amenities_from_raw(paths: list[str]) -> tuple[list[Amenity], Counter, list[str]]:
    """Merge saved batch files; returns (amenities, dropped counts, retrieval timestamps)."""
    merged: dict[str, Amenity] = {}
    dropped: Counter = Counter()
    stamps: list[str] = []
    for p in paths:
        saved = json.loads(Path(p).read_text())
        if saved["_meta"].get("remark"):  # files from older versions of this module
            raise OverpassError(f"{p}: Overpass remark (likely a timeout, data incomplete): {saved['_meta']['remark']}")
        stamps.append(saved["_meta"]["retrieved_at"])
        items, d = amenities_from_elements(saved["elements"], saved["_meta"]["retrieved_at"])
        dropped.update(d)
        for a in items:
            merged.setdefault(a.amenity_id, a)
    return list(merged.values()), dropped, stamps
