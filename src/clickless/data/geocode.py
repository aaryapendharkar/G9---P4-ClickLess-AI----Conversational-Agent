"""Cached geocoder for benchmark places -> data/snapshot/places.json. Owner: Aarya. (ADR 0010)

Only place strings that name a US state ("Flagstaff, AZ", "The Loop, Chicago, IL") are geocoded.
A bare, ambiguous name such as "Springfield" is never looked up, so it stays unresolved and the
system asks the driver instead of guessing (safety-critical field, annotation guide rule 2).

Gazetteer keys (lowercase): the full string ("flagstaff, az") plus the bare city ("flagstaff")
when exactly one geocoded place has that city name, so parser output without the state still
resolves. Source: OSM Nominatim, 1 request/second per its usage policy, responses cached on disk.
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import repo_path

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "ClickLessCharge/0.1 (ASU CSE 573 course project)"
US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND "
    "OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY PR".split()
)
_STATE_SUFFIX = re.compile(r",\s*([A-Za-z]{2})\s*$")


def is_geocodable(place: str) -> bool:
    m = _STATE_SUFFIX.search(place or "")
    return bool(m) and m.group(1).upper() in US_STATES


def _bare_city(place: str) -> str | None:
    parts = [p.strip() for p in place.split(",")]
    return parts[0].lower() if len(parts) == 2 else None  # only "City, ST"


def benchmark_places(items: list[Any]) -> list[str]:
    """Unique origin/destination strings of benchmark items, excluding fields marked missing."""
    out: set[str] = set()
    for it in items:
        for f in ("origin", "destination"):
            text = it.gold_request.get(f)
            if text and f not in it.missing_safety_fields:
                out.add(text.strip())
    return sorted(out)


def geocode_places(
    places: list[str],
    cache_dir: str = "data/cache/geocode",
    *,
    endpoint: str = NOMINATIM_URL,
    pause_s: float = 1.1,
) -> dict[str, dict[str, Any]]:
    """place -> {"lat", "lon", "display_name", "osm", "retrieved_at"} or {"error": ...}."""
    import requests

    cache = repo_path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    results: dict[str, dict[str, Any]] = {}
    for place in places:
        if not is_geocodable(place):
            results[place] = {"error": "not geocoded: no US state in the text (ambiguous on purpose)"}
            continue
        key = re.sub(r"[^a-z0-9]+", "_", place.lower()).strip("_")
        path = cache / f"{key}.json"
        if not path.exists():
            params = {"q": place, "format": "jsonv2", "limit": 1, "countrycodes": "us"}
            for attempt in range(1, 4):
                try:
                    resp = requests.get(endpoint, params=params, headers={"User-Agent": USER_AGENT}, timeout=60)
                    status = resp.status_code
                except requests.RequestException:
                    status = None
                if status == 200:
                    break
                if attempt == 3:
                    raise RuntimeError(f"Nominatim failed for {place!r} (HTTP {status}); rerun to resume")
                time.sleep(pause_s * 5 * attempt)
            stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            path.write_text(json.dumps({"retrieved_at": stamp, "query": place, "results": resp.json()}))
            time.sleep(pause_s)
        saved = json.loads(path.read_text())
        if not saved["results"]:
            results[place] = {"error": "no Nominatim result"}
            continue
        r = saved["results"][0]
        results[place] = {
            "lat": float(r["lat"]),
            "lon": float(r["lon"]),
            "display_name": r.get("display_name"),
            "osm": f"{r.get('osm_type')}/{r.get('osm_id')}",
            "retrieved_at": saved["retrieved_at"],
        }
    return results


def gazetteer(results: dict[str, dict[str, Any]]) -> tuple[dict[str, list[float]], dict[str, Any]]:
    """(records for places.json, details) -- records map lowercase key -> [lat, lon]."""
    records: dict[str, list[float]] = {}
    bare: dict[str, list[str]] = {}
    for place, r in results.items():
        if "error" in r:
            continue
        records[place.lower()] = [r["lat"], r["lon"]]
        city = _bare_city(place)
        if city:
            bare.setdefault(city, []).append(place)
    for city, owners in bare.items():
        if len(owners) == 1 and city not in records:
            records[city] = records[owners[0].lower()]
    details = {
        "source": "OSM Nominatim",
        "unresolved": {p: r["error"] for p, r in results.items() if "error" in r},
        "places": {p: r for p, r in results.items() if "error" not in r},
        "built_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }
    return dict(sorted(records.items())), details


def write_places(results: dict[str, dict[str, Any]], out_path: str | Path) -> Path:
    records, details = gazetteer(results)
    note = "Cached geocodes of benchmark places (data/geocode.py). Keys are lowercase; see _meta.details."
    payload = {"_meta": {"note": note, "details": details}, "records": records}
    out = Path(out_path)
    out.write_text(json.dumps(payload, indent=1))
    return out
