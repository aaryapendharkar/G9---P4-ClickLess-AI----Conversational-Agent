"""Adding a built route cache to a snapshot (issue #36). Owner: Aarya."""

import json
import shutil

from clickless.config import repo_path
from clickless.data import add_routes as ar
from clickless.data.loaders import DataStore
from clickless.data.snapshot import verify_manifest, write_manifest
from clickless.planning.routing import CachedRouter

FIXTURE_ROUTE = repo_path("data/fixtures/routes/phoenix__flagstaff.json")


def _osrm_route(name, route_id="osrm-1"):
    payload = json.loads(FIXTURE_ROUTE.read_text(encoding="utf-8"))
    payload["route_id"] = route_id
    payload["provenance"] = {"source": "OSRM", "source_url": "https://router.project-osrm.org/route/v1/driving/x",
                             "retrieved_at": "2026-11-19T03:13:00Z", "status": "cached"}
    payload["_meta"] = {"origin_text": name.split("__")[0], "built_by": "scripts/build_route_cache.py"}
    return payload


def _put_routes(snap, names, report=None):
    (snap / "routes").mkdir(exist_ok=True)
    for name in names:
        (snap / "routes" / name).write_text(json.dumps(_osrm_route(name)), encoding="utf-8")
    if report is not None:
        (snap / ar.REPORT_FILE).write_text(json.dumps(report), encoding="utf-8")


def test_routes_are_checked_hashed_and_recorded(stations_only):
    before = json.loads((stations_only / "MANIFEST.json").read_text(encoding="utf-8"))
    _put_routes(stations_only, ["phoenix__flagstaff.json", "phoenix-az__flagstaff-az.json"],
                report={"osrm_server": "https://router.project-osrm.org", "pairs_total": 2, "alias_files": True,
                        "failed": [{"pair": ["tempe, az", "nowhere, az"], "reason": "no_route"}],
                        "skipped_items": [{"item": "x", "reason": "unresolved"}]})

    assert ar.add_routes(str(stations_only)) == 0

    after = json.loads((stations_only / "MANIFEST.json").read_text(encoding="utf-8"))
    assert verify_manifest(str(stations_only)) == []
    assert {"routes/phoenix__flagstaff.json", "routes/phoenix-az__flagstaff-az.json", ar.REPORT_FILE} <= set(after["files"])
    for name in ("stations.json", "vehicles.json", "places.json", "amenities.json"):
        assert after["files"][name] == before["files"][name]
    routes = after["sources"]["routes"]
    assert routes["files"] == 2 and routes["distinct_routes"] == 1 and routes["sources"] == {"OSRM": 2}
    assert routes["pairs_failed"] == [["tempe, az", "nowhere, az"]] and routes["items_skipped"] == 1
    assert after["sources"]["afdc"] == before["sources"]["afdc"] and after["frozen"] is False


def test_cached_router_reads_the_added_routes(stations_only, cfg_a):
    _put_routes(stations_only, ["phoenix__flagstaff.json"])
    assert ar.add_routes(str(stations_only)) == 0
    store = DataStore.from_dir(stations_only)
    route = CachedRouter(store, cfg_a["energy"]).route(store.geocode("phoenix"), store.geocode("flagstaff"))
    assert route.route_id == "osrm-1" and route.provenance.source == "OSRM"


def test_bad_route_files_are_refused_and_nothing_changes(stations_only):
    before = (stations_only / "MANIFEST.json").read_text(encoding="utf-8")
    _put_routes(stations_only, ["phoenix__flagstaff.json", "Phoenix to Flagstaff.json"])
    (stations_only / "routes" / "tempe__mesa.json").write_text('{"route_id": "x"}', encoding="utf-8")
    (stations_only / "routes" / "a__b.json.tmp").write_text("{", encoding="utf-8")

    assert ar.add_routes(str(stations_only)) == 1
    assert (stations_only / "MANIFEST.json").read_text(encoding="utf-8") == before
    _, problems = ar.check_route_files(stations_only / "routes")
    assert sorted(p.split(":")[0] for p in problems) == ["Phoenix to Flagstaff.json", "a__b.json.tmp", "tempe__mesa.json"]


def test_refuses_empty_changed_or_frozen_snapshots(stations_only, tmp_path):
    assert ar.add_routes(str(stations_only)) == 1  # routes/ is empty

    frozen = tmp_path / "frozen"
    shutil.copytree(stations_only, frozen)
    write_manifest(str(frozen), extra={"frozen": True})
    _put_routes(frozen, ["phoenix__flagstaff.json"])
    assert ar.add_routes(str(frozen)) == 1

    _put_routes(stations_only, ["phoenix__flagstaff.json"])
    (stations_only / "places.json").write_text("{}", encoding="utf-8")
    assert ar.add_routes(str(stations_only)) == 1
