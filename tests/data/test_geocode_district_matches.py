"""District names resolve to the district, not a hotel, apartments or a road (#122)."""

import json

import pytest

from clickless.config import repo_path
from clickless.data import add_places as add_places_mod
from clickless.data import geocode
from clickless.data.add_places import add_places, known_places
from clickless.data.snapshot import verify_manifest, write_manifest


def _hit(name, addresstype, lat="39.7", lon="-105.0", rank=30):
    return {"lat": lat, "lon": lon, "display_name": name, "osm_type": "way", "osm_id": 1,
            "addresstype": addresstype, "place_rank": rank}


HOTEL = _hit("SpringHill Suites by Marriott Denver Downtown, 1190, Auraria Parkway, Denver", "tourism")
DISTRICT = _hit("Downtown, Denver, Colorado, United States", "suburb", "39.7487", "-104.9951", 20)
CITY = _hit("Denver, Colorado, United States", "city", "39.7392", "-104.9849", 16)


def test_district_words():
    assert geocode.district_word("Downtown Denver, CO") == "downtown"
    assert geocode.district_word("Old Town Scottsdale, AZ") == "old town"
    assert geocode.district_word("West Yellowstone, MT") is None
    assert geocode.district_city("Old Town Scottsdale, AZ") == "Scottsdale, AZ"
    assert geocode.district_city("Midtown Manhattan, NY") == "Manhattan, NY"
    assert geocode.district_city("Downtown, Austin, TX") is None


def test_the_district_beats_a_hotel_named_after_it():
    assert geocode.pick_result("Downtown Denver, CO", [HOTEL, DISTRICT]) is DISTRICT


def test_a_hotel_alone_is_not_accepted():
    assert geocode.pick_result("Downtown Denver, CO", [HOTEL]) is None


@pytest.mark.parametrize(
    "place, display_name",
    [
        ("Downtown Austin, TX", "Downtown, Austin, Travis County, Texas"),
        ("Uptown Dallas, TX", "Uptown PID, Dallas, Dallas County, Texas"),
        ("Downtown San Jose, CA", "Downtown San José, West Santa Clara Street, San Jose"),
        ("Midtown Atlanta, GA", "Midtown, Atlanta, Fulton County, Georgia"),
    ],
)
def test_names_starting_with_the_district_word_are_the_district(place, display_name):
    assert geocode.is_district_result(place, {"display_name": display_name})


@pytest.mark.parametrize(
    "place, display_name, expected",
    [  # the 5 places from #122, as stored (no addresstype), then ones that are fine
        ("Downtown Denver, CO", "SpringHill Suites by Marriott Denver Downtown, 1190, Auraria Parkway", True),
        ("Downtown Detroit, MI", "DoubleTree Suites by Hilton Hotel Detroit Downtown - Fort Shelby, 525", True),
        ("Downtown Minneapolis, MN", "I 94;MN 55, Loring Park, Central, Minneapolis", True),
        ("Downtown Seattle, WA", "Crowne Plaza Seattle Downtown, 1113, 6th Avenue", True),
        ("Old Town Scottsdale, AZ", "The Glen at Old Town Apartments, 4343, Scottsdale", True),
        ("Downtown Phoenix, AZ", "Downtown, Central City, Phoenix, Maricopa County", False),
        ("Uptown Dallas, TX", "Uptown PID, Dallas, Dallas County, Texas", False),
        ("West Yellowstone, MT", "West Yellowstone, Gallatin County, Montana", False),  # a town, not a district
        ("Phoenix Sky Harbor International Airport, Phoenix, AZ", "Phoenix Sky Harbor ..., Terminal 4", False),
    ],
)
def test_stored_district_mismatches_are_recognised(place, display_name, expected):
    details = {"lat": 1.0, "lon": 1.0, "display_name": display_name}
    assert geocode.looks_like_district_mismatch(place, details) is expected


def test_a_stored_city_centre_fallback_is_not_flagged_again():
    details = {"lat": 39.74, "lon": -104.98, "display_name": "Denver, Colorado, United States", "addresstype": "city",
               "fallback": "city centre of 'Denver, CO': no district match in the top 5"}
    assert not geocode.looks_like_district_mismatch("Downtown Denver, CO", details)


def _fake_requests(monkeypatch, answers):
    requests = pytest.importorskip("requests")
    asked = []

    class R:
        status_code = 200

        def __init__(self, q):
            self.q = q

        def json(self):
            return answers[self.q]

    def get(url, params, headers, timeout):
        asked.append(params["q"])
        return R(params["q"])

    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(geocode.time, "sleep", lambda s: None)
    return asked


def test_geocoder_uses_the_district_when_it_is_in_the_top_5(tmp_path, monkeypatch):
    asked = _fake_requests(monkeypatch, {"Downtown Denver, CO": [HOTEL, DISTRICT]})
    out = geocode.geocode_places(["Downtown Denver, CO"], cache_dir=str(tmp_path))
    assert asked == ["Downtown Denver, CO"]
    assert (out["Downtown Denver, CO"]["lat"], out["Downtown Denver, CO"]["addresstype"]) == (39.7487, "suburb")
    assert "fallback" not in out["Downtown Denver, CO"]


def test_geocoder_falls_back_to_the_city_centre_and_says_so(tmp_path, monkeypatch):
    asked = _fake_requests(monkeypatch, {"Downtown Denver, CO": [HOTEL], "Denver, CO": [CITY]})
    out = geocode.geocode_places(["Downtown Denver, CO"], cache_dir=str(tmp_path))
    assert asked == ["Downtown Denver, CO", "Denver, CO"]
    assert out["Downtown Denver, CO"]["lat"] == 39.7392
    assert out["Downtown Denver, CO"]["fallback"].startswith("city centre of 'Denver, CO'")


# ------------------------------------------------------------------ repairing a snapshot


def _route(origin_text, destination_text):
    payload = json.loads(repo_path("data/fixtures/routes/phoenix__flagstaff.json").read_text(encoding="utf-8"))
    payload["_meta"] = {"origin_text": origin_text, "destination_text": destination_text}
    return payload


@pytest.fixture
def snapshot(tmp_path):
    snap = tmp_path / "snapshot"
    (snap / "routes").mkdir(parents=True)
    old = {
        "Downtown Denver, CO": {"lat": 39.7516, "lon": -105.0058, "osm": "relation/14250431",
                                "display_name": "SpringHill Suites by Marriott Denver Downtown, 1190, Auraria Parkway",
                                "retrieved_at": "2026-11-14T08:14:47+00:00"},
        "Elko, NV": {"lat": 41.1958, "lon": -115.3273, "display_name": "Elko County, Nevada, United States",
                     "osm": "relation/166462", "retrieved_at": "2026-11-14T08:14:47+00:00"},
        "Boulder, CO": {"lat": 40.015, "lon": -105.27, "display_name": "Boulder, Boulder County, Colorado",
                        "osm": "relation/2", "retrieved_at": "2026-11-14T08:14:47+00:00"},
    }
    geocode.write_places(old, snap / "places.json")
    for name, (o, d) in {
        "downtown-denver-co__boulder-co.json": ("Downtown Denver, Co", "Boulder, Co"),
        "boulder-co__elko-nv.json": ("Boulder, Co", "Elko, Nv"),
    }.items():
        (snap / "routes" / name).write_text(json.dumps(_route(o, d)), encoding="utf-8")
    write_manifest(str(snap), extra={"frozen": False, "sources": {"places": {"requested": 3, "resolved": 3}}})
    return snap


@pytest.fixture
def benchmark(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    gold = {"origin": "Downtown Denver, CO", "destination": "Boulder, CO", "vehicle_id": "v", "start_soc_pct": 50}
    item = {"id": "deepti-005", "author": "deepti", "category": "urban", "route_type": "urban", "text": "t",
            "gold_request": gold, "clarification_required": False}
    (raw / "deepti.jsonl").write_text(json.dumps(item) + "\n", encoding="utf-8")
    return raw


@pytest.fixture
def fake_nominatim(monkeypatch):
    asked = []

    def fake(places, cache_dir, *, endpoint, pause_s):
        asked.append(list(places))
        return {p: {"lat": 39.7487, "lon": -104.9951, "display_name": "Downtown, Denver, Colorado", "osm": "r/3",
                    "addresstype": "suburb", "place_rank": 20, "retrieved_at": "2026-11-23T03:00:00+00:00"}
                for p in places}

    monkeypatch.setattr(add_places_mod.geocode, "geocode_places", fake)
    return asked


def test_only_the_district_flag_touches_districts(snapshot, benchmark, fake_nominatim):
    """--fix-area-matches alone fixes Elko and leaves the Denver hotel match (and its route) alone."""
    assert add_places(str(snapshot), str(benchmark), fix_area_matches=True) == 0
    assert fake_nominatim == [["Elko, NV"]]
    assert (snapshot / "routes" / "downtown-denver-co__boulder-co.json").exists()


def test_district_mismatch_is_regeocoded_and_its_routes_removed(snapshot, benchmark, fake_nominatim, capsys):
    assert add_places(str(snapshot), str(benchmark), fix_district_matches=True) == 0

    assert fake_nominatim == [["Downtown Denver, CO"]]  # Elko is left for --fix-area-matches
    places = json.loads((snapshot / "places.json").read_text(encoding="utf-8"))
    assert places["records"]["downtown denver, co"] == [39.7487, -104.9951]
    assert known_places(places)["Boulder, CO"]["lat"] == 40.015
    assert sorted(p.name for p in (snapshot / "routes").iterdir()) == ["boulder-co__elko-nv.json"]
    src = json.loads((snapshot / "MANIFEST.json").read_text(encoding="utf-8"))["sources"]["places"]
    assert src["routes_removed"] == ["routes/downtown-denver-co__boulder-co.json"]
    refreshed = [(r["place"], r["reason"]) for r in src["refreshed"]]
    assert refreshed == [("Downtown Denver, CO", "not the district it names")]
    assert verify_manifest(str(snapshot)) == []
    assert "Downtown Denver, CO: not the district it names (was: SpringHill Suites" in capsys.readouterr().out

    assert add_places(str(snapshot), str(benchmark), fix_district_matches=True) == 0  # nothing left
    assert fake_nominatim == [["Downtown Denver, CO"]]
