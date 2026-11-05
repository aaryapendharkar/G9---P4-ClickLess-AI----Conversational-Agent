"""OSM amenity normalization (ADR 0011)."""

import json

import pytest

from clickless.contracts import AmenityCategory
from clickless.data import DataStore
from clickless.data import osm_amenities as osm
from clickless.data.validate import blocking_problems, validate_store

WHEN = "2026-11-03T18:00:00+00:00"


def node(i, tags, lat=35.19, lon=-111.62, **extra):
    return {"type": "node", "id": i, "lat": lat, "lon": lon, "tags": tags, **extra}


@pytest.mark.parametrize(
    "tags, category",
    [
        ({"amenity": "restaurant"}, AmenityCategory.FOOD),
        ({"amenity": "fast_food"}, AmenityCategory.FOOD),
        ({"amenity": "food_court"}, AmenityCategory.FOOD),
        ({"amenity": "cafe"}, AmenityCategory.COFFEE),
        ({"shop": "coffee"}, AmenityCategory.COFFEE),
        ({"amenity": "toilets"}, AmenityCategory.RESTROOM),
        ({"shop": "supermarket"}, AmenityCategory.GROCERY),
        ({"shop": "convenience"}, AmenityCategory.GROCERY),
        ({"tourism": "motel"}, AmenityCategory.LODGING),
        ({"amenity": "fuel"}, AmenityCategory.OTHER),
        ({"shop": "restaurant"}, AmenityCategory.OTHER),
        ({}, AmenityCategory.OTHER),
    ],
)
def test_category_mapping(tags, category):
    assert osm.normalize_category(tags) == category


@pytest.mark.parametrize(
    "value, expected",
    [
        ("yes", "yes"),
        ("designated", "yes"),
        ("limited", "limited"),
        ("no", "no"),
        (None, "unknown"),
        ("maybe", "unknown"),
    ],
)
def test_wheelchair_missing_or_odd_is_unknown(value, expected):
    tags = {"amenity": "toilets"} | ({"wheelchair": value} if value else {})
    assert osm.to_amenity(node(1, tags), WHEN).wheelchair == expected


def test_way_uses_center_and_provenance_is_osm_cached():
    el = {
        "type": "way",
        "id": 42,
        "center": {"lat": 35.2, "lon": -111.6},
        "timestamp": "2025-05-01T10:00:00Z",
        "tags": {"amenity": "restaurant", "name": "Diner", "cuisine": "american", "phone": "555"},
    }
    a = osm.to_amenity(el, WHEN)
    assert a.amenity_id == "osm:way/42" and (a.lat, a.lon) == (35.2, -111.6) and a.name == "Diner"
    assert a.provenance.source == "OSM" and a.provenance.status.value == "cached"
    assert a.provenance.source_url == "https://www.openstreetmap.org/way/42"
    assert a.provenance.source_updated_at.isoformat() == "2025-05-01T10:00:00+00:00"
    assert "phone" not in a.tags and a.tags["cuisine"] == "american"


def test_venue_restroom_uses_toilets_wheelchair_not_entrance_tag():
    gas = node(7, {"amenity": "fuel", "name": "Gas & Go", "toilets": "yes", "wheelchair": "yes"})
    items, dropped = osm.amenities_from_elements([gas], WHEN)
    assert [a.amenity_id for a in items] == ["osm:node/7#toilets"]
    assert items[0].category == AmenityCategory.RESTROOM and items[0].wheelchair == "unknown"
    assert items[0].name == "Restroom at Gas & Go" and not dropped

    cafe = node(8, {"amenity": "cafe", "toilets": "yes", "toilets:wheelchair": "yes"})
    items, _ = osm.amenities_from_elements([cafe], WHEN)
    assert {(a.amenity_id, a.category, a.wheelchair) for a in items} == {
        ("osm:node/8", AmenityCategory.COFFEE, "unknown"),
        ("osm:node/8#toilets", AmenityCategory.RESTROOM, "yes"),
    }


def test_private_toilets_are_not_a_restroom():
    items, dropped = osm.amenities_from_elements(
        [node(9, {"amenity": "fuel", "toilets": "yes", "toilets:access": "private"})], WHEN
    )
    assert items == [] and dropped == {"other_category": 1}


def test_dedup_and_drop_counts():
    els = [
        node(1, {"amenity": "cafe"}),
        node(1, {"amenity": "cafe"}),
        node(2, {"leisure": "park"}),
        {"type": "node", "id": 3, "tags": {"amenity": "cafe"}},
        {"type": "area", "id": 4},
    ]
    items, dropped = osm.amenities_from_elements(els, WHEN)
    assert [a.amenity_id for a in items] == ["osm:node/1"]
    assert dropped == {"other_category": 1, "no_coordinates": 1, "not_an_element": 1}


def test_query_has_one_around_per_station_and_no_polyline(store):
    stations = list(store.stations.values())[:3]
    q = osm.build_overpass_query(stations, 400)
    assert q.startswith("[out:json][timeout:180];") and q.rstrip().endswith("out meta center;")
    assert q.count("around:400,") == 2 * len(stations)
    for line in (ln for ln in q.splitlines() if "around:" in ln):
        assert line.split("around:")[1].split(")")[0].count(",") == 2  # radius,lat,lon only
    assert '["toilets"="yes"]' in q and "restaurant" in q and "supermarket" in q
    with pytest.raises(ValueError):
        osm.build_overpass_query([], 400)


class _Resp:
    def __init__(self, status, payload):
        self.status_code, self._p = status, payload

    def json(self):
        return self._p


def test_fetch_batches_resume_and_strip_editor_names(store, tmp_path, monkeypatch):
    requests = pytest.importorskip("requests")
    calls = []

    def fake_post(url, data, headers, timeout):
        calls.append(data["data"])
        if len(calls) == 1:
            return _Resp(429, {})
        el = node(len(calls), {"amenity": "cafe"}, user="someone", uid=5, changeset=9, timestamp="2025-01-01T00:00:00Z")
        return _Resp(200, {"elements": [el]})

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(osm.time, "sleep", lambda s: None)
    stations = list(store.stations.values())[:3]
    paths = osm.fetch_amenities(stations, str(tmp_path), batch_size=2, progress=False)
    assert len(paths) == 2 and len(calls) == 3  # first batch retried once after 429
    raw = json.loads(open(paths[0]).read())
    assert "user" not in raw["elements"][0] and "uid" not in raw["elements"][0]
    assert osm.fetch_amenities(stations, str(tmp_path), batch_size=2, progress=False) == paths
    assert len(calls) == 3  # cached batches are not downloaded again
    items, _, stamps = osm.amenities_from_raw(paths)
    assert len(items) == 2 and len(stamps) == 2


def test_fetch_gives_up_after_max_attempts(store, tmp_path, monkeypatch):
    requests = pytest.importorskip("requests")
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(504, {}))
    monkeypatch.setattr(osm.time, "sleep", lambda s: None)
    with pytest.raises(osm.OverpassError, match="HTTP 504"):
        osm.fetch_amenities(list(store.stations.values())[:1], str(tmp_path), max_attempts=2, progress=False)


def test_overpass_timeout_remark_is_not_silently_accepted(tmp_path):
    p = tmp_path / "b.json"
    p.write_text(
        json.dumps({"_meta": {"retrieved_at": WHEN, "remark": "runtime error: Query timed out"}, "elements": []})
    )
    with pytest.raises(osm.OverpassError, match="timed out"):
        osm.amenities_from_raw([str(p)])


def test_converted_amenities_pass_validation(tmp_path):
    items, _ = osm.amenities_from_elements(
        [node(1, {"amenity": "cafe"}), node(2, {"amenity": "fuel", "toilets": "yes"}, lat=36.0)], WHEN
    )
    problems = validate_store(DataStore([], [], items, {}, tmp_path))
    assert blocking_problems(problems) == {} and len(items) == 2


def test_timeout_remark_is_retried_and_never_cached(store, tmp_path, monkeypatch):
    requests = pytest.importorskip("requests")
    replies = [_Resp(200, {"elements": [], "remark": "runtime error: Query timed out"}),
               _Resp(200, {"elements": [node(1, {"amenity": "cafe"})]})]  # fmt: skip
    monkeypatch.setattr(requests, "post", lambda *a, **k: replies.pop(0))
    monkeypatch.setattr(osm.time, "sleep", lambda s: None)
    paths = osm.fetch_amenities(list(store.stations.values())[:1], str(tmp_path), progress=False)
    items, _, _ = osm.amenities_from_raw(paths)
    assert replies == [] and [a.amenity_id for a in items] == ["osm:node/1"]


def test_timeout_remark_on_every_attempt_fails_without_writing(store, tmp_path, monkeypatch):
    requests = pytest.importorskip("requests")
    monkeypatch.setattr(requests, "post", lambda *a, **k: _Resp(200, {"elements": [], "remark": "timed out"}))
    monkeypatch.setattr(osm.time, "sleep", lambda s: None)
    with pytest.raises(osm.OverpassError, match="remark"):
        osm.fetch_amenities(list(store.stations.values())[:1], str(tmp_path), max_attempts=2, progress=False)
    assert list(tmp_path.glob("*.json")) == []


def test_non_json_reply_is_retried(store, tmp_path, monkeypatch):
    requests = pytest.importorskip("requests")

    class Html(_Resp):
        def json(self):
            raise ValueError("not json")

    replies = [Html(200, None), _Resp(200, {"elements": [node(1, {"amenity": "cafe"})]})]
    monkeypatch.setattr(requests, "post", lambda *a, **k: replies.pop(0))
    monkeypatch.setattr(osm.time, "sleep", lambda s: None)
    paths = osm.fetch_amenities(list(store.stations.values())[:1], str(tmp_path), progress=False)
    assert replies == [] and len(osm.amenities_from_raw(paths)[0]) == 1
