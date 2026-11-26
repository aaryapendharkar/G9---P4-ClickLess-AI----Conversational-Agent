"""Freezing the reviewed draft as it is (#36): every check must pass, and nothing but the flag changes."""

import hashlib
import json
import shutil

import pytest

from clickless.config import repo_path
from clickless.data import add_routes as ar
from clickless.data import freeze_snapshot as fs
from clickless.data import geocode
from clickless.data.snapshot import verify_manifest, write_manifest

FIXTURE_ROUTE = repo_path("data/fixtures/routes/phoenix__flagstaff.json")


def _item(item_id, origin, destination, missing=()):
    gold = {"origin": origin, "destination": destination, "vehicle_id": "fixture-spark-lr-2025",
            "start_soc_pct": 50}
    return {"id": item_id, "author": "ian", "category": "rural", "route_type": "rural", "text": "t",
            "gold_request": gold, "clarification_required": bool(missing), "missing_safety_fields": list(missing)}


@pytest.fixture
def draft(stations_only):
    """A draft with one cached route (Phoenix -> Flagstaff) and a manifest that matches it."""
    fixture = json.loads(repo_path("data/fixtures/places.json").read_text(encoding="utf-8"))["records"]
    geocode.write_places({f"{name.title()}, AZ": {"lat": lat, "lon": lon, "display_name": name.title(),
                                                  "osm": "node/1", "retrieved_at": "2026-11-14T08:14:47+00:00"}
                          for name, (lat, lon) in fixture.items() if name in {"phoenix", "flagstaff", "tempe"}},
                         stations_only / "places.json")
    payload = json.loads(FIXTURE_ROUTE.read_text(encoding="utf-8"))
    payload["provenance"] = {"source": "OSRM", "retrieved_at": "2026-11-19T03:13:00Z", "status": "cached"}
    (stations_only / "routes" / "phoenix__flagstaff.json").write_text(json.dumps(payload), encoding="utf-8")
    keep = json.loads((stations_only / "MANIFEST.json").read_text(encoding="utf-8"))
    write_manifest(str(stations_only), extra={k: v for k, v in keep.items()
                                              if k not in {"created_at", "files", "record_counts"}})
    return stations_only


@pytest.fixture
def raw(tmp_path):
    d = tmp_path / "raw"
    d.mkdir()
    lines = [
        _item("ian-001", "Phoenix", "Flagstaff"),
        _item("ian-002", "Springfield", "Flagstaff"),  # unresolved on purpose: needs clarification
        _item("ian-003", "Rochester", "Phoenix", missing=["origin"]),
    ]
    (d / "ian.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    return d


@pytest.fixture(autouse=True)
def adrs_accepted(monkeypatch):
    monkeypatch.setattr(fs, "unaccepted_freeze_adrs", lambda: {})


def test_a_good_draft_is_ready(draft, raw):
    assert fs.freeze_problems(draft, str(raw)) == []


def test_freeze_only_flips_the_flag(draft, raw, capsys):
    draft_sha = hashlib.sha256((draft / "MANIFEST.json").read_bytes()).hexdigest()
    before = json.loads((draft / "MANIFEST.json").read_text(encoding="utf-8"))

    assert fs.freeze(str(draft), "snapshot-v1-draft", str(raw)) == 0

    after = json.loads((draft / "MANIFEST.json").read_text(encoding="utf-8"))
    assert after["files"] == before["files"] and after["record_counts"] == before["record_counts"]
    assert after["sources"] == before["sources"]
    assert after["frozen"] is True and after["frozen_at"]
    assert after["frozen_from"] == {"release": "snapshot-v1-draft", "manifest_sha256": draft_sha}
    assert verify_manifest(str(draft)) == []
    assert "FROZEN" in capsys.readouterr().out


def test_a_frozen_snapshot_cannot_be_frozen_or_changed_again(draft, raw):
    assert fs.freeze(str(draft), None, str(raw)) == 0
    assert fs.freeze_problems(draft, str(raw)) == ["already frozen"]
    assert ar.add_routes(str(draft)) == 1


def test_refuses_a_changed_file(draft, raw, capsys):
    (draft / "places.json").write_text("{}", encoding="utf-8")
    assert fs.freeze(str(draft), None, str(raw)) == 1
    assert "does not match its MANIFEST.json" in capsys.readouterr().err
    assert json.loads((draft / "MANIFEST.json").read_text(encoding="utf-8"))["frozen"] is False


def test_refuses_unaccepted_adrs(draft, raw, monkeypatch):
    monkeypatch.setattr(fs, "unaccepted_freeze_adrs", lambda: {"0010-afdc-station-normalization.md": "proposed"})
    assert any("ADRs not accepted" in p for p in fs.freeze_problems(draft, str(raw)))


def test_refuses_a_route_file_missing_from_the_manifest(draft, raw):
    shutil.copy(draft / "routes" / "phoenix__flagstaff.json", draft / "routes" / "tempe__flagstaff.json")
    assert any("not in MANIFEST.json" in p for p in fs.freeze_problems(draft, str(raw)))


def test_refuses_a_benchmark_item_without_a_cached_route(draft, raw):
    (raw / "more.jsonl").write_text(json.dumps(_item("ian-004", "Tempe", "Flagstaff")) + "\n", encoding="utf-8")
    problems = fs.freeze_problems(draft, str(raw))
    assert any("without a cached route" in p and "ian-004" in p for p in problems)
    assert not any("ian-002" in p or "ian-003" in p for p in problems)  # clarification items are fine


def test_refuses_a_county_or_district_match(draft, raw):
    places = {"Elko, NV": {"lat": 41.19, "lon": -115.32, "display_name": "Elko County, Nevada, United States",
                           "osm": "relation/1", "retrieved_at": "2026-11-14T08:14:47+00:00"}}
    geocode.write_places(places, draft / "places.json")
    keep = json.loads((draft / "MANIFEST.json").read_text(encoding="utf-8"))
    extra = {k: v for k, v in keep.items() if k not in {"created_at", "files", "record_counts"}}
    write_manifest(str(draft), extra=extra)
    assert any("Elko, NV" in p for p in fs.freeze_problems(draft, str(raw)))


def test_check_only_changes_nothing(draft, raw, capsys):
    before = (draft / "MANIFEST.json").read_bytes()
    assert fs.main(["--dir", str(draft), "--raw-dir", str(raw), "--check-only"]) == 0
    assert (draft / "MANIFEST.json").read_bytes() == before
    assert "ready to freeze" in capsys.readouterr().out
