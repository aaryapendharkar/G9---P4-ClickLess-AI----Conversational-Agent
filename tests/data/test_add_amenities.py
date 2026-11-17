"""Adding OSM amenities to a stations-only snapshot (issue #36). Owner: Aarya."""

import json
import shutil

import pytest

from clickless.config import repo_path
from clickless.data import add_amenities as aa
from clickless.data import osm_amenities as osm
from clickless.data.loaders import DataStore
from clickless.data.snapshot import verify_manifest, write_manifest


@pytest.fixture
def stations_only(tmp_path):
    """A draft snapshot like snapshot-v1-draft-stations: fixture stations, no amenities."""
    snap = tmp_path / "snapshot"
    snap.mkdir()
    for name in ("stations.json", "vehicles.json", "places.json"):
        shutil.copy(repo_path("data/fixtures") / name, snap / name)
    (snap / "amenities.json").write_text(json.dumps({"_meta": {}, "records": []}), encoding="utf-8")
    (snap / "routes").mkdir()
    write_manifest(str(snap), extra={"built_at": "2026-11-17T17:40:41+00:00", "frozen": False,
                                     "sources": {"afdc": {"raw_file": "afdc_US_x.json"},
                                                 "osm": {"scope": "none", "stations_queried": 0}}})
    return snap


def _fake_overpass(monkeypatch, failing=()):
    requests = pytest.importorskip("requests")
    calls = []

    def post(url, data, headers, timeout):
        calls.append(url)
        if url in failing:
            raise requests.ConnectTimeout("blocked")

        class R:
            status_code = 200

            def json(self):
                return {"elements": [{"type": "node", "id": 900 + len(calls), "lat": 34.56, "lon": -112.06,
                                      "tags": {"amenity": "restaurant", "name": f"Diner {len(calls)}"}}]}
        return R()

    monkeypatch.setattr(requests, "post", post)
    monkeypatch.setattr(osm.time, "sleep", lambda s: None)
    return calls


def test_adds_amenities_and_keeps_other_files(stations_only, tmp_path, monkeypatch):
    before = json.loads((stations_only / "MANIFEST.json").read_text(encoding="utf-8"))
    calls = _fake_overpass(monkeypatch)

    assert aa.add_amenities(str(stations_only), raw_dir=str(tmp_path / "raw")) == 0

    assert calls and set(calls) == {osm.OVERPASS_URL}
    after = json.loads((stations_only / "MANIFEST.json").read_text(encoding="utf-8"))
    assert verify_manifest(str(stations_only)) == []
    for name in ("stations.json", "vehicles.json", "places.json"):
        assert after["files"][name] == before["files"][name]  # untouched
    assert after["record_counts"]["amenities.json"] > 0
    assert after["frozen"] is False and after["built_at"] == before["built_at"]
    assert after["sources"]["afdc"] == before["sources"]["afdc"]
    assert after["sources"]["osm"]["scope"] == "dc_fast" and after["sources"]["osm"]["stations_queried"] > 0
    assert DataStore.from_dir(stations_only).amenities


def test_switches_to_the_mirror_when_the_main_server_is_unreachable(stations_only, tmp_path, monkeypatch):
    calls = _fake_overpass(monkeypatch, failing={osm.OVERPASS_URL})

    assert aa.add_amenities(str(stations_only), raw_dir=str(tmp_path / "raw")) == 0

    assert osm.OVERPASS_URL in calls and osm.OVERPASS_MIRROR_URL in calls
    after = json.loads((stations_only / "MANIFEST.json").read_text(encoding="utf-8"))
    assert after["sources"]["osm"]["endpoint"] == osm.OVERPASS_MIRROR_URL
    assert after["sources"]["osm"]["servers_tried"] == [osm.OVERPASS_URL, osm.OVERPASS_MIRROR_URL]


def test_all_servers_down_keeps_the_snapshot_unchanged(stations_only, tmp_path, monkeypatch):
    before = (stations_only / "MANIFEST.json").read_text(encoding="utf-8")
    _fake_overpass(monkeypatch, failing={osm.OVERPASS_URL, osm.OVERPASS_MIRROR_URL})

    assert aa.add_amenities(str(stations_only), raw_dir=str(tmp_path / "raw")) == 1
    assert (stations_only / "MANIFEST.json").read_text(encoding="utf-8") == before
    assert verify_manifest(str(stations_only)) == []


def test_refuses_frozen_or_changed_snapshots(stations_only, tmp_path):
    (stations_only / "places.json").write_text("{}", encoding="utf-8")
    assert aa.add_amenities(str(stations_only), raw_dir=str(tmp_path / "raw")) == 1

    frozen = tmp_path / "frozen"
    shutil.copytree(stations_only, frozen)
    shutil.copy(repo_path("data/fixtures/places.json"), frozen / "places.json")
    write_manifest(str(frozen), extra={"frozen": True})
    assert aa.add_amenities(str(frozen), raw_dir=str(tmp_path / "raw")) == 1


def test_command_line_accepts_several_servers(monkeypatch):
    seen = {}
    monkeypatch.setattr(aa, "add_amenities", lambda d, **kw: seen.update(dir=d, **kw) or 0)
    assert aa.main(["--dir", "x", "--overpass-url", "https://a/api", "--overpass-url", "https://b/api"]) == 0
    assert seen["dir"] == "x" and seen["servers"] == ["https://a/api", "https://b/api"]
    aa.main([])
    assert tuple(seen["servers"]) == aa.DEFAULT_SERVERS
