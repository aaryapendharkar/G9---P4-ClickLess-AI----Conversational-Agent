"""add_places: geocode only the benchmark places a snapshot lacks, keep the rest, re-hash (#103)."""

import json

import pytest

from clickless.data import add_places as add_places_mod
from clickless.data import geocode
from clickless.data.add_places import add_places, known_places
from clickless.data.snapshot import verify_manifest, write_manifest

OLD = {
    "Flagstaff, AZ": {"lat": 35.19, "lon": -111.65, "display_name": "Flagstaff", "osm": "relation/1",
                      "retrieved_at": "2026-11-14T08:14:47+00:00"},
    "Terminal 4, Phoenix, AZ": {"error": "no Nominatim result"},
}


def _item(item_id, origin, destination, missing=()):
    gold = {"origin": origin, "destination": destination, "vehicle_id": "v", "start_soc_pct": 50}
    return {"id": item_id, "author": "manas", "category": "rural", "route_type": "rural", "text": "t",
            "gold_request": gold, "clarification_required": bool(missing), "missing_safety_fields": list(missing)}


@pytest.fixture
def snapshot(tmp_path):
    snap = tmp_path / "snapshot"
    snap.mkdir()
    geocode.write_places(OLD, snap / "places.json")
    (snap / "stations.json").write_text(json.dumps({"_meta": {}, "records": []}), encoding="utf-8")
    write_manifest(str(snap), extra={"frozen": False, "sources": {
        "afdc": {"raw_file": "afdc_US_x.json"},
        "places": {"source": "OSM Nominatim", "requested": 2, "resolved": 1, "unresolved": ["Terminal 4, Phoenix, AZ"]},
    }})
    return snap


@pytest.fixture
def benchmark(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    lines = [
        "// comment lines are skipped",
        json.dumps(_item("manas-001", "Flagstaff, AZ", "Bangor, ME")),
        json.dumps(_item("manas-002", "Terminal 4, Phoenix, AZ", "Bar Harbor, ME")),
        json.dumps(_item("manas-003", "Rochester", "Buffalo, NY", missing=["origin"])),  # missing: not geocoded
        json.dumps(_item("manas-004", "Springfield", "Bangor, ME")),  # no state: never looked up
    ]
    (raw / "manas.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return raw


@pytest.fixture
def fake_nominatim(monkeypatch):
    asked = []

    def fake(places, cache_dir, *, endpoint, pause_s):
        asked.append(list(places))
        out = {}
        for i, p in enumerate(places):
            if not geocode.is_geocodable(p):
                out[p] = {"error": "not geocoded: no US state in the text (ambiguous on purpose)"}
            else:
                out[p] = {"lat": 44.0 + i, "lon": -68.0 - i, "display_name": p, "osm": f"node/{i}",
                          "retrieved_at": "2026-11-20T06:00:00+00:00"}
        return out

    monkeypatch.setattr(add_places_mod.geocode, "geocode_places", fake)
    return asked


def _places(snap):
    return json.loads((snap / "places.json").read_text(encoding="utf-8"))


def test_only_new_places_are_geocoded_and_old_ones_kept(snapshot, benchmark, fake_nominatim, capsys):
    assert add_places(str(snapshot), str(benchmark)) == 0

    assert fake_nominatim == [["Bangor, ME", "Bar Harbor, ME", "Buffalo, NY", "Springfield"]]
    places = _places(snapshot)
    known = known_places(places)
    assert known["Flagstaff, AZ"] == OLD["Flagstaff, AZ"]  # unchanged, retrieval time included
    assert known["Terminal 4, Phoenix, AZ"] == OLD["Terminal 4, Phoenix, AZ"]  # not retried
    assert places["records"]["bangor, me"] == [44.0, -68.0]
    assert places["records"]["bangor"] == [44.0, -68.0]  # bare city: only one place has it
    assert places["records"]["flagstaff, az"] == [35.19, -111.65]
    assert "springfield" not in places["records"]
    assert "Rochester" not in known

    manifest = json.loads((snapshot / "MANIFEST.json").read_text(encoding="utf-8"))
    src = manifest["sources"]["places"]
    assert src["added"] == ["Bangor, ME", "Bar Harbor, ME", "Buffalo, NY", "Springfield"]
    assert (src["requested"], src["resolved"]) == (6, 4)
    assert src["unresolved"] == ["Springfield", "Terminal 4, Phoenix, AZ"]
    assert src["retrieved_window"] == ["2026-11-14T08:14:47+00:00", "2026-11-20T06:00:00+00:00"]
    assert manifest["sources"]["afdc"] == {"raw_file": "afdc_US_x.json"} and manifest["frozen"] is False
    assert verify_manifest(str(snapshot)) == []
    out = capsys.readouterr().out
    assert "Places added: 3 of 4 new" in out and "not resolved: Springfield" in out
    assert "Geocoding 4 new place(s):\n  Bangor, ME\n  Bar Harbor, ME\n  Buffalo, NY\n  Springfield\n" in out


def test_nothing_new_changes_nothing(snapshot, benchmark, fake_nominatim, capsys):
    assert add_places(str(snapshot), str(benchmark)) == 0
    before = (snapshot / "MANIFEST.json").read_bytes(), (snapshot / "places.json").read_bytes()

    assert add_places(str(snapshot), str(benchmark)) == 0

    assert len(fake_nominatim) == 1
    assert ((snapshot / "MANIFEST.json").read_bytes(), (snapshot / "places.json").read_bytes()) == before
    assert "nothing to add" in capsys.readouterr().out


def test_a_city_name_shared_by_two_places_stops_resolving_bare(snapshot, tmp_path, fake_nominatim):
    raw = tmp_path / "raw2"
    raw.mkdir()
    (raw / "x.jsonl").write_text(json.dumps(_item("x-1", "Flagstaff, AZ", "Flagstaff, ME")) + "\n", encoding="utf-8")
    assert "flagstaff" in _places(snapshot)["records"]

    assert add_places(str(snapshot), str(raw)) == 0

    records = _places(snapshot)["records"]
    assert "flagstaff" not in records  # ambiguous now: the system must ask which one
    assert "flagstaff, az" in records and "flagstaff, me" in records


def test_refuses_a_changed_or_frozen_snapshot(snapshot, benchmark, fake_nominatim, capsys):
    (snapshot / "stations.json").write_text("{}", encoding="utf-8")
    assert add_places(str(snapshot), str(benchmark)) == 1
    assert "does not match its MANIFEST.json" in capsys.readouterr().err

    write_manifest(str(snapshot), extra={"frozen": True})
    assert add_places(str(snapshot), str(benchmark)) == 1
    assert "frozen" in capsys.readouterr().err
    assert fake_nominatim == []
