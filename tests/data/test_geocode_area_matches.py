"""A town or landmark never resolves to the county or state named after it (Elko, NV -> Elko County)."""

import json

import pytest

from clickless.config import repo_path
from clickless.data import add_places as add_places_mod
from clickless.data import geocode
from clickless.data.add_places import add_places, known_places
from clickless.data.add_routes import add_routes
from clickless.data.snapshot import verify_manifest, write_manifest

COUNTY = {"lat": "41.1958", "lon": "-115.3273", "display_name": "Elko County, Nevada, United States",
          "osm_type": "relation", "osm_id": 166462, "addresstype": "county", "place_rank": 12}
TOWN = {"lat": "40.8324", "lon": "-115.7631", "display_name": "Elko, Elko County, Nevada, United States",
        "osm_type": "relation", "osm_id": 170117, "addresstype": "town", "place_rank": 16}
STATE = {"lat": "39.5", "lon": "-116.9", "display_name": "Nevada, United States",
         "osm_type": "relation", "osm_id": 165473, "addresstype": "state", "place_rank": 8}


def test_the_town_is_picked_over_its_county():
    assert geocode.pick_result("Elko, NV", [COUNTY, TOWN]) is TOWN
    assert geocode.pick_result("Elko, NV", [TOWN, COUNTY]) is TOWN


def test_only_areas_matching_leaves_the_place_unresolved():
    assert geocode.pick_result("Elko, NV", [COUNTY, STATE]) is None


def test_a_place_that_names_a_county_may_resolve_to_it():
    assert geocode.pick_result("Elko County, NV", [COUNTY, TOWN]) is COUNTY


def _hit(name, addresstype, place_rank, lat="1"):
    return {"lat": lat, "lon": "1", "display_name": name, "osm_type": "node", "osm_id": 1,
            "addresstype": addresstype, "place_rank": place_rank}


def test_a_town_beats_a_park_named_after_it():
    """Ian's review on #120: [county, Custer State Park, town] -> the town for a bare "Town, ST"."""
    park = _hit("Custer State Park, Custer County, South Dakota", "park", 30)
    town = _hit("Custer, Custer County, South Dakota", "town", 16)
    county = _hit("Custer County, South Dakota", "county", 12)
    assert geocode.pick_result("Custer, SD", [county, park, town]) is town
    assert geocode.pick_result("Custer, SD", [county, park]) is park  # no settlement: first non-area hit


def test_a_landmark_takes_the_first_non_area_hit():
    capitol = _hit("Texas State Capitol, Austin, Texas", "tourism", 30)
    city = _hit("Austin, Travis County, Texas", "city", 16)
    assert geocode.pick_result("Texas State Capitol, Austin, TX", [city, capitol]) is city
    assert geocode.pick_result("Texas State Capitol, Austin, TX", [capitol, city]) is capitol


@pytest.mark.parametrize("place", ["Arizona State University, Tempe, AZ", "State College, PA"])
def test_the_word_state_inside_a_name_is_still_filtered(place):
    county = _hit("Centre County, Pennsylvania", "county", 12)
    other = _hit("State College, Centre County, Pennsylvania", "town", 16)
    assert geocode.pick_result(place, [county, other]) is other


@pytest.mark.parametrize("place", ["Elko County, NV", "Nome Census Area, AK", "Orleans Parish, LA"])
def test_only_a_name_ending_in_an_area_word_skips_the_filter(place):
    county = _hit("Some County", "county", 12)
    assert geocode.pick_result(place, [county]) is county


def test_place_rank_alone_marks_an_area():
    assert geocode.is_area_result({"place_rank": 12}) and not geocode.is_area_result({"place_rank": 16})


@pytest.mark.parametrize(
    "place, display_name, expected",
    [
        ("Elko, NV", "Elko County, Nevada, United States", True),  # stored before v2: no addresstype
        ("Cherokee, NC", "Cherokee County, North Carolina, United States", True),
        ("Elko, NV", "Elko, Elko County, Nevada, United States", False),
        ("Monument Valley, AZ", "Monument Valley Navajo Tribal Park, Navajo County, Arizona", False),
        ("Uptown Dallas, TX", "Uptown PID, Dallas, Dallas County, Texas, United States", False),
        ("Elko County, NV", "Elko County, Nevada, United States", False),  # asked for the county
        ("Nome, AK", "Nome Census Area, Alaska, United States", True),
        ("Ely, NV", "Nevada, United States", True),  # resolved to the whole state
        ("Downtown Austin, TX", "Texas State Capitol, 1100, Congress Avenue, Austin, Texas", False),
        ("Arizona State University, Tempe, AZ", "Arizona State University, Tempe, Maricopa County, Arizona", False),
        ("Washington, DC", "Washington, District of Columbia, United States", False),
        ("Downtown Seattle, WA", "Washington, United States", True),
    ],
)
def test_stored_area_matches_are_recognised(place, display_name, expected):
    assert geocode.looks_like_area_match(place, {"lat": 1.0, "lon": 1.0, "display_name": display_name}) is expected


def test_geocoder_asks_for_five_hits_and_keeps_the_town(tmp_path, monkeypatch):
    requests = pytest.importorskip("requests")
    seen = []

    class R:
        status_code = 200

        def json(self):
            return [COUNTY, TOWN]

    monkeypatch.setattr(requests, "get", lambda url, params, headers, timeout: seen.append(params) or R())
    monkeypatch.setattr(geocode.time, "sleep", lambda s: None)
    out = geocode.geocode_places(["Elko, NV"], cache_dir=str(tmp_path))
    assert seen[0]["limit"] == 5
    assert (out["Elko, NV"]["lat"], out["Elko, NV"]["addresstype"]) == (40.8324, "town")
    assert [p.name for p in tmp_path.glob("*.json")] == [f"elko_nv.{geocode.CACHE_VERSION}.json"]


# ------------------------------------------------------------------ repairing a snapshot


def _route(origin_text, destination_text):
    """A valid Route file (the fixture route's geometry) carrying the builder's _meta block."""
    payload = json.loads(repo_path("data/fixtures/routes/phoenix__flagstaff.json").read_text(encoding="utf-8"))
    payload["_meta"] = {"origin_text": origin_text, "destination_text": destination_text}
    return payload


@pytest.fixture
def snapshot(tmp_path):
    snap = tmp_path / "snapshot"
    (snap / "routes").mkdir(parents=True)
    old = {
        "Elko, NV": {"lat": 41.1958, "lon": -115.3273, "display_name": "Elko County, Nevada, United States",
                     "osm": "relation/166462", "retrieved_at": "2026-11-14T08:14:47+00:00"},
        "Winnemucca, NV": {"lat": 40.973, "lon": -117.735, "display_name": "Winnemucca, Humboldt County, Nevada",
                           "osm": "relation/1", "retrieved_at": "2026-11-14T08:14:47+00:00"},
    }
    geocode.write_places(old, snap / "places.json")
    for name, (o, d) in {
        "winnemucca-nv__elko-nv.json": ("Winnemucca, Nv", "Elko, Nv"),
        "winnemucca__elko.json": ("Winnemucca, Nv", "Elko, Nv"),  # alias copy, same texts
        "elko__twin-falls.json": ("Elko", "Twin Falls, Id"),  # reached through the bare-city key
        "winnemucca-nv__reno-nv.json": ("Winnemucca, Nv", "Reno, Nv"),  # untouched
    }.items():
        (snap / "routes" / name).write_text(json.dumps(_route(o, d)), encoding="utf-8")
    write_manifest(str(snap), extra={"frozen": False, "sources": {"places": {"requested": 2, "resolved": 2}}})
    return snap


@pytest.fixture
def fake_nominatim(monkeypatch):
    asked = []

    def fake(places, cache_dir, *, endpoint, pause_s):
        asked.append(list(places))
        return {p: {"lat": 40.8324, "lon": -115.7631, "display_name": "Elko, Elko County, Nevada", "osm": "relation/2",
                    "addresstype": "town", "place_rank": 16, "retrieved_at": "2026-11-21T08:00:00+00:00"}
                for p in places}

    monkeypatch.setattr(add_places_mod.geocode, "geocode_places", fake)
    return asked


@pytest.fixture
def benchmark(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    gold = {"origin": "Winnemucca, NV", "destination": "Elko, NV", "vehicle_id": "v", "start_soc_pct": 50}
    item = {"id": "hriday-014", "author": "hriday", "category": "rural", "route_type": "rural", "text": "t",
            "gold_request": gold, "clarification_required": False}
    (raw / "hriday.jsonl").write_text(json.dumps(item) + "\n", encoding="utf-8")
    return raw


def test_main_survives_a_non_latin_name_on_a_cp1252_console(snapshot, benchmark, monkeypatch):
    """Ian's review on #120: printing "Cherokee;ᏣᎳᎩ" crashed after a complete write on Windows."""
    import io
    import sys

    def fake(places, cache_dir, *, endpoint, pause_s):
        return {p: {"lat": 40.83, "lon": -115.76, "display_name": "Elko;ᏣᎳᎩ, Nevada", "osm": "r/2",
                    "addresstype": "town", "place_rank": 16, "retrieved_at": "2026-11-23T00:00:00+00:00"}
                for p in places}

    monkeypatch.setattr(add_places_mod.geocode, "geocode_places", fake)
    console = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", console)
    assert add_places_mod.main(["--dir", str(snapshot), "--benchmark-dir", str(benchmark), "--fix-area-matches"]) == 0
    console.flush()
    assert b"to Elko;???, Nevada" in console.buffer.getvalue()  # replaced, not a UnicodeEncodeError


def test_without_the_flag_nothing_changes(snapshot, benchmark, fake_nominatim):
    before = (snapshot / "MANIFEST.json").read_bytes()
    assert add_places(str(snapshot), str(benchmark)) == 0
    assert fake_nominatim == [] and (snapshot / "MANIFEST.json").read_bytes() == before


def test_county_match_is_regeocoded_and_its_routes_removed(snapshot, benchmark, fake_nominatim, capsys):
    assert add_places(str(snapshot), str(benchmark), fix_area_matches=True) == 0

    assert fake_nominatim == [["Elko, NV"]]
    places = json.loads((snapshot / "places.json").read_text(encoding="utf-8"))
    assert places["records"]["elko, nv"] == [40.8324, -115.7631] == places["records"]["elko"]
    assert known_places(places)["Winnemucca, NV"]["lat"] == 40.973  # untouched
    assert sorted(p.name for p in (snapshot / "routes").iterdir()) == ["winnemucca-nv__reno-nv.json"]

    manifest = json.loads((snapshot / "MANIFEST.json").read_text(encoding="utf-8"))
    src = manifest["sources"]["places"]
    assert src["routes_removed"] == ["routes/elko__twin-falls.json", "routes/winnemucca-nv__elko-nv.json",
                                     "routes/winnemucca__elko.json"]
    assert [(r["place"], r["was_at"], r["now_at"]) for r in src["refreshed"]] == [
        ("Elko, NV", [41.1958, -115.3273], [40.8324, -115.7631])
    ]
    assert "added" not in src  # nothing new was added
    assert verify_manifest(str(snapshot)) == []
    out = capsys.readouterr().out
    assert "Elko, NV (was: Elko County, Nevada, United States)" in out and "moved 54 km" in out

    # a second run finds nothing left to fix
    assert add_places(str(snapshot), str(benchmark), fix_area_matches=True) == 0
    assert fake_nominatim == [["Elko, NV"]]


def test_add_routes_accepts_the_rebuilt_routes_afterwards(snapshot, benchmark, fake_nominatim):
    """The removed routes left the manifest with add_places, so the rebuilt ones count as new."""
    assert add_places(str(snapshot), str(benchmark), fix_area_matches=True) == 0
    rebuilt = _route("Winnemucca, Nv", "Elko, Nv")
    (snapshot / "routes" / "winnemucca-nv__elko-nv.json").write_text(json.dumps(rebuilt), encoding="utf-8")

    assert add_routes(str(snapshot)) == 0
    manifest = json.loads((snapshot / "MANIFEST.json").read_text(encoding="utf-8"))
    assert "routes/winnemucca-nv__elko-nv.json" in manifest["files"] and verify_manifest(str(snapshot)) == []
