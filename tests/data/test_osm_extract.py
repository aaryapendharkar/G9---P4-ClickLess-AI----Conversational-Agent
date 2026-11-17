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
