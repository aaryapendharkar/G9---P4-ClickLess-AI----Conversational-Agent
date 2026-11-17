"""OSM amenities from Geofabrik extracts (issue #36). Owner: Aarya.

Feature lines follow `osmium export -f geojsonseq -a type,id,timestamp` (and the older
--add-unique-id=type_id form: n123 / w123 / a<2*way> / a<2*relation+1>).
"""

import json

from clickless.data import add_amenities as aa
from clickless.data import osm_extract as ox
from clickless.data.loaders import DataStore
from clickless.data.osm_amenities import amenities_from_raw
from clickless.data.snapshot import verify_manifest

LAT, LON = 33.866, -112.14  # a fixture DC station (Anthem)


def point(i, tags, lat=LAT + 0.001, lon=LON, ts="2025-05-01T10:00:00Z"):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {"@type": "node", "@id": i, "@timestamp": ts, **tags}}


def square(uid, tags, lat=LAT + 0.002, lon=LON, d=0.0003, props_type=None):
    ring = [[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]]
    props = dict(tags)
    if props_type:
        props.update({"@type": props_type[0], "@id": props_type[1]})
    return {"type": "Feature", "id": uid, "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": props}


def test_filter_expressions_match_the_overpass_tags():
    assert ox.filter_expressions() == [
        "nwr/amenity=cafe,fast_food,food_court,restaurant,toilets",
        "nwr/shop=coffee,convenience,greengrocer,supermarket",
        "nwr/tourism=hotel,motel",
        "nwr/toilets=yes",
    ]


def test_ids_from_attributes_and_from_unique_ids():
    assert ox._type_and_id(point(7, {})) == ("node", 7)
    assert ox._type_and_id({"id": "w42", "properties": {}}) == ("way", 42)
    assert ox._type_and_id({"id": "a84", "properties": {}}) == ("way", 42)  # area from way 42
    assert ox._type_and_id({"id": "a85", "properties": {}}) == ("relation", 42)  # from relation 42
    assert ox._type_and_id({"id": "x1", "properties": {}}) is None


def test_feature_becomes_an_overpass_style_element():
    el, pts = ox.feature_to_element(point(7, {"amenity": "cafe", "name": "Bean"}))
    assert el == {"type": "node", "id": 7, "tags": {"amenity": "cafe", "name": "Bean"},
                  "lat": LAT + 0.001, "lon": LON, "timestamp": "2025-05-01T10:00:00Z"}
    el, pts = ox.feature_to_element(square("a84", {"amenity": "restaurant"}))
    assert el["type"] == "way" and el["id"] == 42 and len(pts) == 4  # closing vertex dropped
    assert abs(el["center"]["lat"] - (LAT + 0.00215)) < 1e-6


def test_only_features_near_a_station_are_kept_once(store):
    index = ox.StationIndex([store.station(sid) for sid in store.stations], 400)
    features = [
        point(1, {"amenity": "restaurant"}),                          # ~110 m away: kept
        square("w42", {"amenity": "restaurant"}),                     # building as a line ...
        square("a84", {"amenity": "restaurant"}),                     # ... and as an area: kept once
        square("a85", {"tourism": "hotel"}),                          # relation 42: different object
        point(2, {"amenity": "cafe"}, lat=LAT + 0.02),                # ~2.2 km away: dropped
        {"type": "Feature", "id": "n3", "geometry": None, "properties": {}},  # no geometry
    ]
    elements, stats = ox.match_features(features, index)
    assert sorted((e["type"], e["id"]) for e in elements) == [("node", 1), ("relation", 42), ("way", 42)]
    assert stats["duplicate_geometry"] == 1 and stats["not_near_a_station"] == 1
    assert stats["unusable_feature"] == 1


def test_large_building_counts_when_an_edge_vertex_is_in_range(store):
    index = ox.StationIndex([store.station(sid) for sid in store.stations], 400)
    big = square("a100", {"shop": "supermarket"}, lat=LAT + 0.003, d=0.01)  # center ~0.9 km away
    elements, _ = ox.match_features([big], index)
    assert [(e["type"], e["id"]) for e in elements] == [("way", 50)]


def test_end_to_end_into_a_stations_only_snapshot(stations_only, tmp_path):
    seq = tmp_path / "us-west.geojsonseq"
    lines = [
        point(1, {"amenity": "restaurant", "name": "Diner", "toilets": "yes", "toilets:wheelchair": "yes"}),
        square("a84", {"amenity": "cafe", "name": "Bean"}),
        point(2, {"amenity": "fuel"}),                         # matched by toilets? no: dropped as OTHER
        point(3, {"amenity": "cafe"}, lat=LAT + 0.05),         # far away
    ]
    seq.write_text("".join("\x1e" + json.dumps(f) + "\n" for f in lines), encoding="utf-8")
    raw = tmp_path / "raw"

    path, stats = ox.write_extract_raw(str(stations_only), str(seq), name="us-west",
                                       source="https://download.geofabrik.de/north-america/us/us-west-latest.osm.pbf",
                                       timestamp="2026-11-16T21:21:02Z", out_dir=str(raw))
    assert path.name == "extract_us-west.json" and stats["near_a_station"] == 3
    amenities, dropped, stamps = amenities_from_raw([str(path)])
    assert stamps == ["2026-11-16T21:21:02Z"] and dropped == {"other_category": 1}
    assert {a.amenity_id for a in amenities} == {"osm:node/1", "osm:node/1#toilets", "osm:way/42"}

    assert aa.add_amenities(str(stations_only), from_raw=[str(path)]) == 0
    assert verify_manifest(str(stations_only)) == []
    manifest = json.loads((stations_only / "MANIFEST.json").read_text(encoding="utf-8"))
    osm = manifest["sources"]["osm"]
    assert osm["servers_tried"] == [] and osm["extracts"][0]["endpoint"].endswith("us-west-latest.osm.pbf")
    assert osm["retrieved_window"] == ["2026-11-16T21:21:02Z", "2026-11-16T21:21:02Z"]
    store = DataStore.from_dir(stations_only)
    restroom = next(a for a in store.amenities if a.amenity_id == "osm:node/1#toilets")
    assert restroom.wheelchair == "yes"
    assert next(a for a in store.amenities if a.amenity_id == "osm:node/1").provenance.source_updated_at


def test_extract_with_other_radius_is_refused(stations_only, tmp_path):
    seq = tmp_path / "x.geojsonseq"
    seq.write_text(json.dumps(point(1, {"amenity": "cafe"})) + "\n", encoding="utf-8")
    path, _ = ox.write_extract_raw(str(stations_only), str(seq), name="x", source="s", timestamp="2026-11-16T00:00:00Z",
                                   radius_m=250, out_dir=str(tmp_path / "raw"))
    assert aa.add_amenities(str(stations_only), from_raw=[str(path)]) == 1


def test_cli_filters_prints_one_line(capsys):
    assert ox.main(["filters"]) == 0
    assert capsys.readouterr().out.strip() == " ".join(ox.filter_expressions())


def _index(*entries):
    """A Geofabrik index (index-v1-nogeom.json shape): (id, parent, pbf url)."""
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"id": i, "parent": p, "urls": {"pbf": u} if u else {}}}
        for i, p, u in entries]}


GF = "https://download.geofabrik.de"


def test_us_extracts_are_the_states_and_territories_only():
    index = _index(
        ("us", "north-america", f"{GF}/north-america/us-latest.osm.pbf"),
        ("us/alabama", "us", f"{GF}/north-america/us/alabama-latest.osm.pbf"),
        ("us/georgia", "us", f"{GF}/north-america/us/georgia-latest.osm.pbf"),
        ("georgia", "europe", f"{GF}/europe/georgia-latest.osm.pbf"),           # the country
        ("us-midwest", "us", f"{GF}/north-america/us/us-midwest-latest.osm.pbf"),  # overlaps states
        ("us/california/norcal", "us/california", f"{GF}/north-america/us/california/norcal-latest.osm.pbf"),
        ("us/puerto-rico", "us", f"{GF}/north-america/us/puerto-rico-latest.osm.pbf"),
        ("us/us-virgin-islands", "us", f"{GF}/north-america/us/us-virgin-islands-latest.osm.pbf"),
        ("us/nowhere", "us", None),
        ("delaware", "united-states", f"{GF}/north-america/us/delaware-latest.osm.pbf"),  # any parent label
    )
    assert [ox.extract_name(u) for u in ox.us_extract_urls(index)] == [
        "alabama", "delaware", "georgia", "puerto-rico", "us-virgin-islands"]


def test_fallback_list_covers_every_state_dc_and_territories():
    urls = ox.fallback_us_extract_urls()
    assert len(urls) == 53 and len(set(urls)) == 53
    assert all(u.startswith(f"{GF}/north-america/us/") and u.endswith("-latest.osm.pbf") for u in urls)
    assert f"{GF}/north-america/us/delaware-latest.osm.pbf" in urls  # the link the smoke test downloaded


def test_groups_cover_every_url_once():
    urls = ox.fallback_us_extract_urls()
    groups = ox.split_into_groups(urls, 8)
    assert len(groups) == 8 and sorted(u for g in groups for u in g) == sorted(urls)
    assert max(map(len, groups)) - min(map(len, groups)) <= 1
    assert ox.split_into_groups(urls[:3], 8) == [[u] for u in sorted(urls[:3])]


def test_regions_cli_prints_a_matrix_from_an_index_file(tmp_path, capsys):
    states = [(f"us/s{i}", "us", f"{GF}/north-america/us/s{i:02d}-latest.osm.pbf") for i in range(52)]
    path = tmp_path / "index.json"
    path.write_text(json.dumps(_index(*states)), encoding="utf-8")
    assert ox.main(["regions", "--index", str(path), "--groups", "4"]) == 0
    matrix = json.loads(capsys.readouterr().out)
    assert [g["group"] for g in matrix["include"]] == ["1", "2", "3", "4"]
    assert sum(len(g["urls"].split()) for g in matrix["include"]) == 52


def test_regions_cli_falls_back_when_the_index_is_unreadable(tmp_path, capsys):
    assert ox.main(["regions", "--index", str(tmp_path / "missing.json"), "--groups", "8"]) == 0
    out = capsys.readouterr()
    assert "::warning" in out.err
    assert sum(len(g["urls"].split()) for g in json.loads(out.out)["include"]) == 53


def test_regions_cli_refuses_a_short_list(tmp_path, capsys):
    path = tmp_path / "index.json"
    path.write_text(json.dumps(_index(("us/alabama", "us", f"{GF}/north-america/us/alabama-latest.osm.pbf"))),
                    encoding="utf-8")
    assert ox.main(["regions", "--index", str(path)]) == 1
    assert "::error" in capsys.readouterr().err


def test_index_sample_explains_an_unexpected_index():
    assert "top-level keys" in ox.index_sample({"type": "x"})
    sample = ox.index_sample(_index(("us/alabama", "us", f"{GF}/north-america/us/alabama-latest.osm.pbf")))
    assert "1 entries" in sample and "alabama" in sample
