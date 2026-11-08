"""Snapshot build end to end, offline: saved AFDC file + stubbed Overpass/Nominatim (ADR 0010/0011)."""

import json
from types import SimpleNamespace

import pytest

from clickless.data import DataStore, build_snapshot, geocode, osm_amenities
from clickless.data.snapshot import verify_manifest

RETRIEVED = "2026-11-03T18:00:00+00:00"


def _station(i, lat, lon, units=True, status="E"):
    rec = {
        "id": i, "station_name": f"Station {i}", "latitude": lat, "longitude": lon, "access_code": "public",
        "status_code": status, "ev_network": "Electrify America", "ev_connector_types": ["J1772COMBO"],
        "ev_dc_fast_num": 2, "updated_at": "2025-01-01T00:00:00Z",
    }  # fmt: skip
    if units:
        rec["ev_charging_units"] = [
            {"charging_level": "dc_fast", "connectors": {"J1772COMBO": {"power_kw": 150, "port_count": 2}}}
        ]
    return rec


@pytest.fixture
def raw_afdc(tmp_path):
    records = [
        _station(1, 35.19, -111.62),
        _station(2, 34.56, -111.85, units=False),  # older record: power unknown -> warning only
        _station(3, 13.47, 144.75),  # Guam: outside the validator's US boxes -> skipped, counted
        _station(4, 33.45, -112.07, status="T"),
        {**_station(5, 33.40, -111.90), "ev_connector_types": ["NEMA515"], "ev_charging_units": []},
    ]
    p = tmp_path / "afdc_US_20261103T180000Z.json"
    p.write_text(json.dumps({"_meta": {"retrieved_at": RETRIEVED, "url": "x", "params": {}, "total_results": 5},
                             "response": {"fuel_stations": records}}))  # fmt: skip
    return p


def test_build_writes_loadable_validated_snapshot(tmp_path, raw_afdc, monkeypatch):
    def fake_fetch(stations, **kw):
        p = tmp_path / "osm.json"
        el = [{"type": "node", "id": 9, "lat": 35.191, "lon": -111.621, "tags": {"amenity": "cafe"}}]
        p.write_text(json.dumps({"_meta": {"retrieved_at": RETRIEVED, "station_ids": []}, "elements": el}))
        fake_fetch.queried = [s.station_id for s in stations]
        return [str(p)]

    def fake_geocode(places, **kw):
        return {p: ({"lat": 35.0, "lon": -111.0, "display_name": p, "osm": "node/1", "retrieved_at": RETRIEVED}
                    if geocode.is_geocodable(p) else {"error": "ambiguous"}) for p in places}  # fmt: skip

    monkeypatch.setattr(osm_amenities, "fetch_amenities", fake_fetch)
    monkeypatch.setattr(geocode, "geocode_places", fake_geocode)
    out = tmp_path / "snap"
    rc = build_snapshot.main(["--out", str(out), "--afdc-raw", str(raw_afdc)])
    assert rc == 0

    store = DataStore.from_dir(out)
    assert sorted(store.stations) == ["afdc:1", "afdc:2", "afdc:4"]
    assert fake_fetch.queried == ["afdc:1", "afdc:2"]  # available DC stations only (default scope)
    assert [a.amenity_id for a in store.amenities] == ["osm:node/9"]
    assert len(store.vehicles) == 15 and store.vehicle("nissan-leaf-sv-plus-2024").usable_kwh == 59.0
    assert store.geocode("Flagstaff, AZ").resolved and store.geocode("flagstaff").resolved
    assert not store.geocode("Springfield").resolved  # ambiguous on purpose
    assert (out / "routes").is_dir()

    manifest = json.loads((out / "MANIFEST.json").read_text())
    assert verify_manifest(str(out)) == []
    assert manifest["record_counts"]["stations.json"] == 3 and manifest["record_counts"]["vehicles.json"] == 15
    conv = manifest["sources"]["afdc"]["conversion"]
    assert conv["skipped"] == {"outside_us_bounding_boxes": 1, "no_supported_connector": 1}
    assert manifest["validation"]["warnings"] == {"dc_power_unknown": 1}
    places = manifest["sources"]["places"]
    assert places["unresolved"] == [] and places["requested"] >= 60  # "Springfield" never even requested
    assert places["resolved"] == places["requested"] and places["retrieved_window"] == [RETRIEVED, RETRIEVED]
    assert manifest["sources"]["afdc"]["retrieved_at"] == RETRIEVED
    assert manifest["sources"]["osm"]["retrieved_window"] == [RETRIEVED, RETRIEVED]
    assert manifest["sources"]["afdc"]["params"] == {} and manifest["sources"]["osm"]["radius_m"] == 400


def test_build_refuses_to_freeze_blocking_problems(tmp_path, raw_afdc, monkeypatch, capsys):
    # Let the Guam station past the builder's own filter; the validator still uses the real US
    # boxes, finds a coordinates problem, and the build must stop without writing a manifest.
    monkeypatch.setattr(build_snapshot, "in_us", lambda lat, lon: True)
    rc = build_snapshot.main(["--out", str(tmp_path / "s"), "--afdc-raw", str(raw_afdc), "--osm-scope", "none",
                              "--skip-geocode"])  # fmt: skip
    assert rc == 1
    err = capsys.readouterr().err
    assert "NOT frozen" in err and "coordinates" in err
    assert not (tmp_path / "s" / "MANIFEST.json").exists()


def test_missing_api_key_is_a_clear_error(tmp_path, monkeypatch):
    monkeypatch.delenv("NREL_API_KEY", raising=False)
    monkeypatch.setattr(build_snapshot, "_read_dotenv", lambda p: None)
    with pytest.raises(ValueError, match="NREL_API_KEY"):
        build_snapshot.main(["--out", str(tmp_path / "s"), "--states", "ZZ", "--osm-scope", "none", "--skip-geocode"])


def test_geocodable_rules_and_gazetteer_keys():
    assert geocode.is_geocodable("Flagstaff, AZ") and geocode.is_geocodable("The Loop, Chicago, IL")
    assert not geocode.is_geocodable("Springfield") and not geocode.is_geocodable("Paris, XX")
    res = {
        "Flagstaff, AZ": {"lat": 35.2, "lon": -111.6},
        "Portland, OR": {"lat": 45.5, "lon": -122.7},
        "Portland, ME": {"lat": 43.7, "lon": -70.3},
        "The Loop, Chicago, IL": {"lat": 41.88, "lon": -87.63},
        "Springfield": {"error": "ambiguous"},
    }
    records, details = geocode.gazetteer(res)
    assert records["flagstaff"] == records["flagstaff, az"] == [35.2, -111.6]
    assert "portland" not in records and "portland, or" in records  # bare name ambiguous -> no alias
    assert "the loop" not in records and "springfield" not in records
    assert details["unresolved"] == {"Springfield": "ambiguous"}


def test_benchmark_places_skip_fields_marked_missing():
    items = [
        SimpleNamespace(gold_request={"origin": "Springfield", "destination": "Chicago, IL"},
                        missing_safety_fields=["origin"]),
        SimpleNamespace(gold_request={"origin": "Tempe, AZ"}, missing_safety_fields=[]),
    ]  # fmt: skip
    assert geocode.benchmark_places(items) == ["Chicago, IL", "Tempe, AZ"]


def test_geocode_caches_and_never_queries_ambiguous_names(tmp_path, monkeypatch):
    requests = pytest.importorskip("requests")
    calls = []

    class R:
        status_code = 200

        def json(self):
            return [{"lat": "35.2", "lon": "-111.6", "display_name": "Flagstaff", "osm_type": "relation", "osm_id": 1}]

    def fake_get(url, params, headers, timeout):
        calls.append(params["q"])
        assert "User-Agent" in headers and "@" not in headers["User-Agent"]
        return R()

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(geocode.time, "sleep", lambda s: None)
    out = geocode.geocode_places(["Flagstaff, AZ", "Springfield"], cache_dir=str(tmp_path))
    out2 = geocode.geocode_places(["Flagstaff, AZ"], cache_dir=str(tmp_path))
    assert calls == ["Flagstaff, AZ"] and out["Flagstaff, AZ"]["lat"] == 35.2 == out2["Flagstaff, AZ"]["lat"]
    assert "error" in out["Springfield"]


def test_geocode_retries_then_gives_a_clear_error(tmp_path, monkeypatch):
    requests = pytest.importorskip("requests")

    class Busy:
        status_code = 503

    calls = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: calls.append(1) or Busy())
    monkeypatch.setattr(geocode.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="rerun to resume"):
        geocode.geocode_places(["Tempe, AZ"], cache_dir=str(tmp_path))
    assert len(calls) == 3 and list(tmp_path.glob("*.json")) == []


# ---- issue #32: integrity on load, config selection, freeze gate ----


def _draft_snapshot(tmp_path, raw_afdc):
    out = tmp_path / "snap"
    rc = build_snapshot.main(["--out", str(out), "--afdc-raw", str(raw_afdc), "--osm-scope", "none",
                              "--skip-geocode"])  # fmt: skip
    assert rc == 0
    return out


def _cfg(snapshot_dir, source="snapshot"):
    return {"data": {"source": source, "fixtures_dir": "data/fixtures", "snapshot_dir": str(snapshot_dir)}}


def test_default_build_is_a_draft(tmp_path, raw_afdc):
    out = _draft_snapshot(tmp_path, raw_afdc)
    assert json.loads((out / "MANIFEST.json").read_text())["frozen"] is False


def test_config_loads_verified_snapshot(tmp_path, raw_afdc):
    out = _draft_snapshot(tmp_path, raw_afdc)
    store = DataStore.from_config(_cfg(out))
    assert sorted(store.stations) == ["afdc:1", "afdc:2", "afdc:4"]


def test_env_var_switches_a_fixture_config_to_the_snapshot(tmp_path, raw_afdc, monkeypatch):
    out = _draft_snapshot(tmp_path, raw_afdc)
    assert "FIX-ST-001" in DataStore.from_config(_cfg(out, source="fixtures")).stations
    monkeypatch.setenv("CLICKLESS_DATA_SOURCE", "snapshot")
    assert "afdc:1" in DataStore.from_config(_cfg(out, source="fixtures")).stations
    monkeypatch.setenv("CLICKLESS_DATA_SOURCE", "live")
    with pytest.raises(ValueError, match="'fixtures' or 'snapshot'"):
        DataStore.from_config(_cfg(out))


def test_changed_or_missing_snapshot_file_is_refused(tmp_path, raw_afdc):
    out = _draft_snapshot(tmp_path, raw_afdc)
    stations = out / "stations.json"
    stations.write_text(stations.read_text().replace("Station 1", "Station X"))
    with pytest.raises(ValueError, match="stations.json"):
        DataStore.from_config(_cfg(out))
    stations.unlink()
    with pytest.raises(ValueError, match="stations.json"):
        DataStore.from_config(_cfg(out))


def test_missing_snapshot_explains_how_to_get_one(tmp_path):
    with pytest.raises(FileNotFoundError, match="snapshot-build"):
        DataStore.from_config(_cfg(tmp_path / "nothing"))


def test_freeze_refused_until_adrs_accepted(tmp_path, raw_afdc, monkeypatch, capsys):
    from clickless.data import snapshot

    decisions = tmp_path / "decisions"
    decisions.mkdir()
    for name in snapshot.FREEZE_ADRS:
        (decisions / name).write_text("# ADR\n\n- Status: proposed\n")
    real = snapshot.unaccepted_freeze_adrs
    monkeypatch.setattr(build_snapshot, "unaccepted_freeze_adrs", lambda: real(str(decisions)))
    rc = build_snapshot.main(["--out", str(tmp_path / "s"), "--afdc-raw", str(raw_afdc), "--osm-scope", "none",
                              "--skip-geocode", "--freeze"])  # fmt: skip
    assert rc == 2 and "0009-vehicle-table-third-party-sources.md" in capsys.readouterr().err
    assert not (tmp_path / "s").exists()


def test_freeze_allowed_once_adrs_accepted(tmp_path, raw_afdc, monkeypatch):
    from clickless.data import snapshot

    decisions = tmp_path / "decisions"
    decisions.mkdir()
    for name in snapshot.FREEZE_ADRS:
        (decisions / name).write_text("# ADR\n\n- Status: accepted (2 approvals)\n")
    real = snapshot.unaccepted_freeze_adrs
    monkeypatch.setattr(build_snapshot, "unaccepted_freeze_adrs", lambda: real(str(decisions)))
    rc = build_snapshot.main(["--out", str(tmp_path / "s"), "--afdc-raw", str(raw_afdc), "--osm-scope", "none",
                              "--skip-geocode", "--freeze"])  # fmt: skip
    assert rc == 0 and json.loads((tmp_path / "s" / "MANIFEST.json").read_text())["frozen"] is True


def test_adr_status_reading(tmp_path):
    from clickless.data.snapshot import unaccepted_freeze_adrs

    (tmp_path / "0009-vehicle-table-third-party-sources.md").write_text("- Status: Accepted\n")
    (tmp_path / "0010-afdc-station-normalization.md").write_text("- Status: proposed\n")
    assert unaccepted_freeze_adrs(str(tmp_path)) == {
        "0010-afdc-station-normalization.md": "proposed",
        "0011-osm-amenity-mapping.md": "missing",
    }
