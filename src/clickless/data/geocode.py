"""Cached geocoder for benchmark places -> data/snapshot/places.json. Owner: Aarya. (ADR 0010)

Only place strings that name a US state ("Flagstaff, AZ", "The Loop, Chicago, IL") are geocoded.
A bare, ambiguous name such as "Springfield" is never looked up, so it stays unresolved and the
system asks the driver instead of guessing (safety-critical field, annotation guide rule 2).

Gazetteer keys (lowercase): the full string ("flagstaff, az") plus the bare city ("flagstaff")
when exactly one geocoded place has that city name, so parser output without the state still
resolves. Source: OSM Nominatim, 1 request/second per its usage policy, responses cached on disk.

Nominatim's first hit for a small town can be the county named after it ("Elko, NV" -> Elko County,
whose centre is 54 km from Elko). A county or state is never what a "Town, ST" or "Landmark, City, ST"
place means, so the first result that is not one is used, out of the top 5; if only counties or
states match, the place stays unresolved instead of being placed tens of km away.

District names ("Downtown Denver, CO", "Old Town Scottsdale, AZ") can likewise come back as a hotel,
an apartment block or a highway that merely has the word in its name, 0.5-2 km from the district
(#122). For those the first hit that is the district itself (a suburb/neighbourhood/quarter, or a
name starting with the district word) is used; if none is in the top 5, the city's centre
("Denver, CO") is used instead and the result says so (`fallback`).
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
AREA_TYPES = frozenset({"county", "state", "country"})
# A name *ending* in one of these is a county-level area ("Elko County", "Nome Census Area"); a word
# like "State" elsewhere is not ("Arizona State University", "State College", "Texas State Capitol").
_AREA_SUFFIX = re.compile(r"\b(county|parish|borough|census area)\s*$", re.I)
SETTLEMENT_TYPES = frozenset({"city", "town", "village", "hamlet", "municipality"})
STATE_NAMES = frozenset(
    "Alabama Alaska Arizona Arkansas California Colorado Connecticut Delaware Florida Georgia Hawaii Idaho "
    "Illinois Indiana Iowa Kansas Kentucky Louisiana Maine Maryland Massachusetts Michigan Minnesota "
    "Mississippi Missouri Montana Nebraska Nevada Ohio Oklahoma Oregon Pennsylvania Tennessee Texas Utah "
    "Vermont Virginia Washington Wisconsin Wyoming".split()
) | {"New Hampshire", "New Jersey", "New Mexico", "New York", "North Carolina", "North Dakota",
     "Rhode Island", "South Carolina", "South Dakota", "West Virginia", "District of Columbia", "Puerto Rico"}
DISTRICT_TYPES = frozenset({"suburb", "neighbourhood", "quarter", "city_district", "borough", "district"})
_DISTRICT_PREFIX = re.compile(r"^(downtown|old town|uptown|midtown)\b\s*", re.I)
CACHE_VERSION = "v2"  # v1 files kept only the first result (limit=1), which may be a county


def is_geocodable(place: str) -> bool:
    m = _STATE_SUFFIX.search(place or "")
    return bool(m) and m.group(1).upper() in US_STATES


def _bare_city(place: str) -> str | None:
    parts = [p.strip() for p in place.split(",")]
    return parts[0].lower() if len(parts) == 2 else None  # only "City, ST"


def _asks_for_an_area(place: str) -> bool:
    return bool(_AREA_SUFFIX.search(place.split(",")[0].strip()))


def is_area_result(result: dict[str, Any]) -> bool:
    """True for a Nominatim hit that is a county, state or country rather than a town or landmark."""
    if (result.get("addresstype") or "").lower() in AREA_TYPES:
        return True
    rank = result.get("place_rank")
    return rank is not None and int(rank) <= 12  # Nominatim: country 4, state 8, county 12, city 16


def district_word(place: str) -> str | None:
    """'Downtown' for "Downtown Denver, CO", None for a place that doesn't name a district."""
    m = _DISTRICT_PREFIX.match(place.split(",")[0].strip())
    return m.group(1).lower() if m else None


def district_city(place: str) -> str | None:
    """The city a district name sits in: "Downtown Denver, CO" -> "Denver, CO" (None if not a district,
    or if nothing but the district word is left, as in "Downtown, Austin, TX")."""
    word = district_word(place)
    if word is None:
        return None
    first, rest = place.split(",", 1)
    city = _DISTRICT_PREFIX.sub("", first.strip()).strip()
    return f"{city},{rest}" if city else None


def is_district_result(place: str, result: dict[str, Any]) -> bool:
    """Is this hit the district `place` names: a suburb/neighbourhood/quarter-type area, or a name that
    starts with the district word ("Downtown", "Uptown PID", "Downtown San José")? A hotel or road
    that only contains the word ("SpringHill Suites by Marriott Denver Downtown") is not."""
    if (result.get("addresstype") or "").lower() in DISTRICT_TYPES:
        return True
    word = district_word(place)
    first = (result.get("display_name") or "").split(",")[0].strip().lower()
    return word is not None and first.startswith(word)


def pick_result(place: str, results: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The hit to use, out of Nominatim's top results.

    - A place that names a county ("Elko County, NV"): the first hit.
    - "Town, ST": the first settlement (city/town/village/hamlet/municipality), so "Custer, SD" is the
      town, not Custer State Park; failing that, the first hit that is not a county/state/country.
    - "Landmark, City, ST": the first hit that is not a county/state/country.
    - A district ("Downtown Denver, CO"): the first hit that is the district (is_district_result).
    None when nothing suitable matches."""
    if _asks_for_an_area(place):
        return results[0] if results else None
    candidates = [r for r in results if not is_area_result(r)]
    if district_word(place) is not None:  # "Downtown Denver, CO": the district itself, or nothing
        return next((r for r in candidates if is_district_result(place, r)), None)
    if len(place.split(",")) == 2:
        settlement = next((r for r in candidates if (r.get("addresstype") or "").lower() in SETTLEMENT_TYPES), None)
        if settlement is not None:
            return settlement
    return candidates[0] if candidates else None


def looks_like_district_mismatch(place: str, details: dict[str, Any]) -> bool:
    """For a stored geocode of a district name: did it resolve to something that isn't the district
    (a hotel, apartments, a highway)? A stored city-centre fallback is accepted."""
    if "error" in details or district_word(place) is None or details.get("fallback"):
        return False
    return not is_district_result(place, details)


def looks_like_area_match(place: str, details: dict[str, Any]) -> bool:
    """For a stored geocode: did a town or landmark resolve to a county/state? A match makes
    add_places --fix-area-matches delete routes, so for entries stored before CACHE_VERSION v2 (no
    addresstype) only the display name's first part decides, and only when it ends in County, Parish,
    Borough or Census Area, or the whole name is a state ("Nevada, United States")."""
    if "error" in details or _asks_for_an_area(place):
        return False
    if details.get("addresstype") is not None or details.get("place_rank") is not None:
        return is_area_result(details)
    parts = [x.strip() for x in (details.get("display_name") or "").split(",")]
    whole_state = len(parts) == 2 and parts[0] in STATE_NAMES and parts[1] == "United States"
    return bool(_AREA_SUFFIX.search(parts[0])) or whole_state  # not "Washington, District of Columbia, ..."


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
        saved = _search(place, cache, endpoint, pause_s, requests)
        if not saved["results"]:
            results[place] = {"error": "no Nominatim result"}
            continue
        r = pick_result(place, saved["results"])
        fallback = None
        if r is None and (city := district_city(place)) is not None:
            city_saved = _search(city, cache, endpoint, pause_s, requests)
            r = pick_result(city, city_saved["results"])
            if r is not None:
                saved, fallback = city_saved, f"city centre of {city!r}: no district match in the top 5"
        if r is None:
            reason = "no district match" if district_word(place) else "only a county or state matched"
            results[place] = {"error": f"{reason}; name the town or landmark more precisely"}
            continue
        results[place] = {
            "lat": float(r["lat"]),
            "lon": float(r["lon"]),
            "display_name": r.get("display_name"),
            "osm": f"{r.get('osm_type')}/{r.get('osm_id')}",
            "addresstype": r.get("addresstype"),
            "place_rank": r.get("place_rank"),
            "retrieved_at": saved["retrieved_at"],
        }
        if fallback:
            results[place]["fallback"] = fallback
    return results


def _search(query: str, cache, endpoint: str, pause_s: float, requests) -> dict[str, Any]:
    """Nominatim's top 5 for `query`, from the disk cache or one polite request (1/s, 3 tries)."""
    key = re.sub(r"[^a-z0-9]+", "_", query.lower()).strip("_")
    path = cache / f"{key}.{CACHE_VERSION}.json"
    if not path.exists():
        params = {"q": query, "format": "jsonv2", "limit": 5, "countrycodes": "us"}
        for attempt in range(1, 4):
            try:
                resp = requests.get(endpoint, params=params, headers={"User-Agent": USER_AGENT}, timeout=60)
                status = resp.status_code
            except requests.RequestException:
                status = None
            if status == 200:
                break
            if attempt == 3:
                raise RuntimeError(f"Nominatim failed for {query!r} (HTTP {status}); rerun to resume")
            time.sleep(pause_s * 5 * attempt)
        stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        payload = {"retrieved_at": stamp, "query": query, "results": resp.json()}
        path.write_text(json.dumps(payload), encoding="utf-8")
        time.sleep(pause_s)
    return json.loads(path.read_text(encoding="utf-8"))


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
    out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return out
