"""Snapshot report / pack / verify (issue #36), on an offline-built snapshot."""

import json
import zipfile

import pytest

from clickless.config import repo_path
from clickless.data import build_snapshot, geocode, osm_amenities, snapshot_report
from evaluation.schema import load_items

RETRIEVED = "2026-11-03T18:00:00+00:00"


def _rec(i, lat, lon, units=True):
    r = {"id": i, "station_name": f"S{i}", "latitude": lat, "longitude": lon, "access_code": "public",
         "status_code": "E", "ev_network": "Electrify America", "ev_connector_types": ["J1772COMBO"],
         "ev_dc_fast_num": 2, "updated_at": "2025-01-01T00:00:00Z"}  # fmt: skip
    if units:
        r["ev_charging_units"] = [
            {"charging_level": "dc_fast", "connectors": {"J1772COMBO": {"power_kw": 150, "port_count": 2}}}
        ]
    return r


@pytest.fixture
def built(tmp_path, monkeypatch):
    raw = tmp_path / "afdc_US_x.json"
    raw.write_text(json.dumps({
        "_meta": {"retrieved_at": RETRIEVED, "url": "x", "params": {"fuel_type": "ELEC"}, "total_results": 2},
        "response": {"fuel_stations": [_rec(1, 35.19, -111.62), _rec(2, 34.56, -111.85, units=False)]},
    }))  # fmt: skip

    def fake_fetch(stations, **kw):
        p = tmp_path / "osm.json"
        els = [{"type": "node", "id": 9, "lat": 35.19, "lon": -111.62, "tags": {"amenity": "cafe"}}]
        p.write_text(json.dumps({"_meta": {"retrieved_at": RETRIEVED, "station_ids": []}, "elements": els}))
        return [str(p)]

    def fake_geocode(places, **kw):
        return {p: ({"lat": 35.0, "lon": -111.0, "display_name": p, "osm": "node/1", "retrieved_at": RETRIEVED}
                    if geocode.is_geocodable(p) else {"error": "ambiguous"}) for p in places}  # fmt: skip

    monkeypatch.setattr(osm_amenities, "fetch_amenities", fake_fetch)
    monkeypatch.setattr(geocode, "geocode_places", fake_geocode)
    out = tmp_path / "snap"
    assert build_snapshot.main(["--out", str(out), "--afdc-raw", str(raw)]) == 0
    return out


def test_report_has_every_issue_36_field(built):
    r = snapshot_report.collect(built)
    assert r["verify_manifest"] == [] and r["blocking"] == {}
    c = r["counts"]
    assert c["stations"] == 2 and c["port_groups"] == 2 and c["physical_ports"] == 4
    assert c["dc_port_groups_with_unknown_power"] == 1 and r["warnings"] == {"dc_power_unknown": 1}
    assert c["amenities"] == 1 and c["vehicles"] == 15 and c["resolved_places"] >= 60
    cov = r["coverage"]
    n_items = len(load_items(repo_path("benchmark/requests/raw")))  # 60 today, 150 when complete
    assert cov["items"] == n_items
    assert cov["both_endpoints_resolved"] + cov["place_marked_missing_on_purpose"] == n_items

    md = snapshot_report.to_markdown(r)
    needles = [
        "draft, not frozen", "verify_manifest()", "`[]`", "Blocking validation problems: none",
        "dc_power_unknown", "| AFDC | 2026-11-03", "| OSM Overpass |", "| Nominatim |",
        "| stations | 2 |", "| vehicles | 15 |", "Station scope: **whole US**", f"of {n_items} requests",
    ]  # fmt: skip
    for needle in needles:
        assert needle in md, needle


def test_pack_then_teammate_verify_roundtrip(built, tmp_path, capsys):
    z = tmp_path / "results" / "share.zip"  # parent folder created on demand
    assert snapshot_report.main(["--dir", str(built), "--pack", str(z)]) == 0
    names = zipfile.ZipFile(z).namelist()
    assert "MANIFEST.json" in names and "stations.json" in names and "routes/" in names

    mine = tmp_path / "teammate"
    zipfile.ZipFile(z).extractall(mine)
    assert snapshot_report.main(["--dir", str(mine), "--verify-only"]) == 0
    assert "(intact)" in capsys.readouterr().out
    assert (mine / "routes").is_dir()

    (mine / "amenities.json").write_text("{}")  # corrupted download
    assert snapshot_report.main(["--dir", str(mine), "--verify-only"]) == 1
    (mine / "places.json").unlink()  # partial download
    r = snapshot_report.collect(mine)
    assert set(r["verify_manifest"]) == {"amenities.json", "places.json"} and "counts" not in r
    assert snapshot_report.main(["--dir", str(mine)]) == 1
    assert "MISMATCH" in snapshot_report.to_markdown(r)


def test_report_saved_to_file(built, tmp_path):
    out = tmp_path / "report.md"
    assert snapshot_report.main(["--dir", str(built), "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("## Snapshot report")


def test_missing_snapshot_is_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="make snapshot-build"):
        snapshot_report.collect(tmp_path)
    assert snapshot_report.main(["--dir", str(tmp_path), "--verify-only"]) == 1
