"""OSM amenities from Geofabrik extract files instead of Overpass. Owner: Aarya. (Issue #36, ADR 0011)

The public Overpass servers could not serve the full-US query (main server refused connections,
the mirror needed minutes per batch). The same OSM data comes in Geofabrik's regional .osm.pbf
extracts. The workflow `snapshot amenities` (method: extract) does, per region:

    osmium tags-filter REGION.osm.pbf $(python -m clickless.data.osm_extract filters) -o f.osm.pbf
    osmium export f.osm.pbf -f geojsonseq -u type_id -a type,id,timestamp -o f.geojsonseq
    python -m clickless.data.osm_extract match --dir data/snapshot --geojsonseq f.geojsonseq \\
        --name us-west --source URL --timestamp REPLICATION_TIMESTAMP

`match` keeps the features within `radius_m` of a target station (same targets and radius as the
Overpass query) and writes data/raw/osm/extract_<name>.json in the Overpass batch format, so
`add_amenities --from-raw` normalizes them with the same code (categories, restrooms, dedup).

Difference from Overpass `around`: a way/relation counts if any of its vertices (or its center) is
within the radius, instead of any point on its outline. For buildings this is nearly the same; the
stored location is the mean of the vertices (Overpass: bounding-box center).
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from ..common.geo import haversine_km
from ..config import repo_path
from .build_snapshot import osm_targets
from .loaders import DataStore
from .osm_amenities import CATEGORY_TAGS

_CELL_DEG = 0.01  # grid cell for the station index (~1.1 km of latitude)
_MAX_VERTICES = 400  # vertices checked per feature (plus its center); enough for any building
_TYPES = {"n": "node", "w": "way", "r": "relation"}

# Geofabrik publishes the exact download URL of every extract in this index ("urls" -> "pbf").
GEOFABRIK_INDEX = "https://download.geofabrik.de/index-v1-nogeom.json"
# Used only if the index cannot be read: the US state/territory extracts (north-america/us/<slug>).
US_EXTRACT_SLUGS: tuple[str, ...] = (
    "alabama", "alaska", "arizona", "arkansas", "california", "colorado", "connecticut", "delaware",
    "district-of-columbia", "florida", "georgia", "hawaii", "idaho", "illinois", "indiana", "iowa",
    "kansas", "kentucky", "louisiana", "maine", "maryland", "massachusetts", "michigan", "minnesota",
    "mississippi", "missouri", "montana", "nebraska", "nevada", "new-hampshire", "new-jersey",
    "new-mexico", "new-york", "north-carolina", "north-dakota", "ohio", "oklahoma", "oregon",
    "pennsylvania", "rhode-island", "south-carolina", "south-dakota", "tennessee", "texas", "utah",
    "vermont", "virginia", "washington", "west-virginia", "wisconsin", "wyoming", "puerto-rico",
    "us-virgin-islands",
)  # fmt: skip
_CENSUS_REGIONS = {"us-midwest", "us-northeast", "us-pacific", "us-south", "us-west"}


def extract_name(url: str) -> str:
    """'.../us/new-york-latest.osm.pbf' -> 'new-york'."""
    return url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".osm.pbf").removesuffix("-latest")


def us_extract_urls(index: dict[str, Any]) -> list[str]:
    """Download URLs of every US state/territory extract in Geofabrik's index (not the census regions,
    which overlap the states, and not sub-state extracts such as Northern California)."""
    urls = set()
    for feature in index.get("features") or []:
        props = feature.get("properties") or {}
        url = (props.get("urls") or {}).get("pbf")
        if props.get("parent") == "us" and url and extract_name(url) not in _CENSUS_REGIONS:
            urls.add(url)
    return sorted(urls)


def fallback_us_extract_urls() -> list[str]:
    return [f"https://download.geofabrik.de/north-america/us/{s}-latest.osm.pbf" for s in US_EXTRACT_SLUGS]


def split_into_groups(urls: list[str], n_groups: int) -> list[list[str]]:
    """Round-robin over the alphabetical list, so big states spread over the groups."""
    n = max(1, min(n_groups, len(urls)))
    return [g for g in (sorted(urls)[i::n] for i in range(n)) if g]


def filter_expressions() -> list[str]:
    """osmium tags-filter expressions for exactly the tags the Overpass query asks for."""
    exprs = [f"nwr/{key}={','.join(sorted(values))}" for key, values in CATEGORY_TAGS]
    exprs.append("nwr/toilets=yes")
    return exprs


def _flatten(coords: Any) -> list[tuple[float, float]]:
    """GeoJSON coordinates of any depth -> [(lon, lat), ...]."""
    if not coords:
        return []
    if isinstance(coords[0], (int, float)):
        return [(float(coords[0]), float(coords[1]))]
    out: list[tuple[float, float]] = []
    for c in coords:
        out.extend(_flatten(c))
    return out


def _type_and_id(feature: dict[str, Any]) -> tuple[str, int] | None:
    """The original OSM object. The unique id (--add-unique-id=type_id) is decoded first because its
    encoding is documented: n123, w123, a<2*way id>, a<2*relation id + 1>; the @type/@id
    attributes (-a type,id) are the fallback."""
    uid = str(feature.get("id") or "")
    if len(uid) > 1 and uid[0] in _TYPES and uid[1:].lstrip("-").isdigit():
        return _TYPES[uid[0]], int(uid[1:])
    if len(uid) > 1 and uid[0] == "a" and uid[1:].isdigit():
        n = int(uid[1:])
        return ("way", n // 2) if n % 2 == 0 else ("relation", (n - 1) // 2)
    props = feature.get("properties") or {}
    if props.get("@type") in {"node", "way", "relation"} and props.get("@id") is not None:
        return props["@type"], int(props["@id"])
    return None


def feature_to_element(feature: dict[str, Any]) -> tuple[dict[str, Any], list[tuple[float, float]]] | None:
    """osmium GeoJSON feature -> (Overpass-style element, vertices as (lat, lon)); None if unusable."""
    ident = _type_and_id(feature)
    geom = feature.get("geometry") or {}
    lonlat = _flatten(geom.get("coordinates"))
    if ident is None or not lonlat:
        return None
    etype, eid = ident
    props = feature.get("properties") or {}
    tags = {k: str(v) for k, v in props.items() if not k.startswith("@")}
    pts = [(lat, lon) for lon, lat in lonlat]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]  # closed ring: the first vertex is repeated at the end
    element: dict[str, Any] = {"type": etype, "id": eid, "tags": tags}
    if etype == "node":
        element["lat"], element["lon"] = pts[0]
    else:
        element["center"] = {
            "lat": round(sum(p[0] for p in pts) / len(pts), 7),
            "lon": round(sum(p[1] for p in pts) / len(pts), 7),
        }
    if props.get("@timestamp"):
        element["timestamp"] = str(props["@timestamp"])
    return element, pts


class StationIndex:
    """Grid index over target stations for 'any station within radius_m' lookups."""

    def __init__(self, stations: Iterable[Any], radius_m: float) -> None:
        self.radius_km = radius_m / 1000.0
        self.cells: dict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
        for s in stations:
            self.cells[self._cell(s.lat, s.lon)].append((s.lat, s.lon))

    @staticmethod
    def _cell(lat: float, lon: float) -> tuple[int, int]:
        return math.floor(lat / _CELL_DEG), math.floor(lon / _CELL_DEG)

    def near(self, lat: float, lon: float) -> bool:
        dlat = self.radius_km / 111.32
        dlon = self.radius_km / (111.32 * max(math.cos(math.radians(lat)), 0.01))
        ci, cj = self._cell(lat, lon)
        ri, rj = math.ceil(dlat / _CELL_DEG), math.ceil(dlon / _CELL_DEG)
        for i in range(ci - ri, ci + ri + 1):
            for j in range(cj - rj, cj + rj + 1):
                for slat, slon in self.cells.get((i, j), ()):
                    if haversine_km(lat, lon, slat, slon) <= self.radius_km:
                        return True
        return False


def read_geojsonseq(path: str | Path) -> Iterator[dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip().lstrip("\x1e").strip()  # GeoJSON text sequences may start with RS
            if line:
                yield json.loads(line)


def match_features(
    features: Iterable[dict[str, Any]], index: StationIndex
) -> tuple[list[dict[str, Any]], Counter]:
    """Elements of the features near any target station, one per OSM object."""
    kept: dict[tuple[str, int], dict[str, Any]] = {}
    stats: Counter = Counter()
    for feature in features:
        stats["features"] += 1
        parsed = feature_to_element(feature)
        if parsed is None:
            stats["unusable_feature"] += 1
            continue
        element, pts = parsed
        key = (element["type"], element["id"])
        if key in kept:
            stats["duplicate_geometry"] += 1  # e.g. a closed way exported as line and as area
            continue
        center = element.get("center") or {"lat": element.get("lat"), "lon": element.get("lon")}
        candidates = [(center["lat"], center["lon"]), *pts[:_MAX_VERTICES]]
        if any(index.near(lat, lon) for lat, lon in candidates):
            kept[key] = element
            stats["near_a_station"] += 1
        else:
            stats["not_near_a_station"] += 1
    return list(kept.values()), stats


def write_extract_raw(
    snapshot_dir: str,
    geojsonseq: str,
    *,
    name: str,
    source: str,
    timestamp: str,
    radius_m: int = 400,
    scope: str = "dc_fast",
    out_dir: str = "data/raw/osm",
) -> tuple[Path, Counter]:
    store = DataStore.from_dir(repo_path(snapshot_dir))
    targets = osm_targets(list(store.stations.values()), scope)
    elements, stats = match_features(read_geojsonseq(geojsonseq), StationIndex(targets, radius_m))
    out = repo_path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"extract_{name}.json"
    meta = {
        "retrieved_at": timestamp,  # the extract's OSM replication timestamp: data as of this time
        "endpoint": source,
        "method": "geofabrik extract + osmium tags-filter/export",
        "radius_m": radius_m,
        "scope": scope,
        "stations_queried": len(targets),
        "match_stats": dict(stats),
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps({"_meta": meta, "elements": elements}), encoding="utf-8")
    tmp.replace(path)
    return path, stats


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("filters", help="print the osmium tags-filter expressions, space-separated")
    r = sub.add_parser("regions", help="print a GitHub Actions matrix of US extract URLs (JSON)")
    r.add_argument("--index", default=GEOFABRIK_INDEX, help="index URL or a saved index file")
    r.add_argument("--groups", type=int, default=8)
    r.add_argument("--min-count", type=int, default=50, help="fail if the index lists fewer extracts")
    m = sub.add_parser("match", help="keep exported features near snapshot stations")
    m.add_argument("--dir", default="data/snapshot")
    m.add_argument("--geojsonseq", required=True)
    m.add_argument("--name", required=True, help="region name, used in the output file name")
    m.add_argument("--source", required=True, help="URL of the extract")
    m.add_argument("--timestamp", required=True, help="the extract's replication timestamp (ISO 8601)")
    m.add_argument("--radius-m", type=int, default=400)
    m.add_argument("--osm-scope", choices=["dc_fast", "all"], default="dc_fast")
    m.add_argument("--out-dir", default="data/raw/osm")
    args = p.parse_args(argv)
    if args.cmd == "filters":
        print(" ".join(filter_expressions()))
        return 0
    if args.cmd == "regions":
        try:
            if args.index.startswith("http"):
                import requests

                resp = requests.get(args.index, timeout=120)
                resp.raise_for_status()
                index = resp.json()
            else:
                index = json.loads(Path(args.index).read_text(encoding="utf-8"))
            urls = us_extract_urls(index)
        except Exception as e:  # noqa: BLE001 - any failure: fall back to the known list, loudly
            print(f"::warning title=Geofabrik index::could not read {args.index} ({type(e).__name__}: {e}); "
                  "using the built-in list of US extracts", file=sys.stderr)
            urls = fallback_us_extract_urls()
        if len(urls) < args.min_count:
            print(f"::error title=Geofabrik index::only {len(urls)} US extracts found (expected at least "
                  f"{args.min_count})", file=sys.stderr)
            return 1
        groups = split_into_groups(urls, args.groups)
        print(json.dumps({"include": [{"group": str(i + 1), "urls": " ".join(g)} for i, g in enumerate(groups)]}))
        return 0
    path, stats = write_extract_raw(
        args.dir, args.geojsonseq, name=args.name, source=args.source, timestamp=args.timestamp,
        radius_m=args.radius_m, scope=args.osm_scope, out_dir=args.out_dir,
    )
    print(f"  {args.name}: {stats['near_a_station']} OSM objects near stations "
          f"(of {stats['features']} features) -> {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
