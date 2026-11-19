"""scripts/build_route_cache.py with OSRM mocked: slug rule, alias copies, skip-existing, provenance."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import requests

from clickless.contracts import Place, Route
from clickless.data import DataStore
from clickless.planning.routing import CachedRouter

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("build_route_cache", ROOT / "scripts/build_route_cache.py")
brc = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = brc  # dataclasses resolve annotations through sys.modules
_spec.loader.exec_module(brc)

OSRM = "http://osrm.test"
FLAGSTAFF, PHOENIX, PAGE = (35.1983, -111.6513), (33.4484, -112.074), (36.9147, -111.4558)
# The snapshot gazetteer holds "City, ST" and the bare city (when unique) at the same point.
PLACES = {"flagstaff": FLAGSTAFF, "flagstaff, az": FLAGSTAFF, "phoenix": PHOENIX, "phoenix, az": PHOENIX,
          "page, az": PAGE}


class _Resp:
    def __init__(self, url, status=200, payload=None):
        self.url, self.status_code, self._payload = url, status, payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def json(self):
        return self._payload


class FakeOSRM(requests.Session):
    """Answers every route request with a 2-point route; `script` overrides statuses per call."""

    def __init__(self, script=()):
        super().__init__()
        self.calls, self.script = [], list(script)

    def get(self, url, params=None, timeout=None):
        full = f"{url}?overview={params['overview']}&geometries={params['geometries']}&steps={params['steps']}"
        self.calls.append(full)
        status = self.script.pop(0) if self.script else 200
        if status == "NoRoute":
            return _Resp(full, 200, {"code": "NoRoute", "message": "Impossible route between points"})
        coords = url.rsplit("/", 1)[1].split(";")
        geometry = [[float(x) for x in c.split(",")] for c in coords]  # [lon, lat]
        return _Resp(full, status, {"code": "Ok", "routes": [{"distance": 233000.0, "duration": 8400.0,
                                                                  "geometry": {"coordinates": geometry}}]})


def items_dir(tmp_path, *pairs):
    raw = tmp_path / "raw"
    raw.mkdir()
    lines = ["// comment lines are skipped"]
    for n, (o, d) in enumerate(pairs, 1):
        gold = {k: v for k, v in (("origin", o), ("destination", d)) if v}
        lines.append(json.dumps({"id": f"t-{n:03d}", "gold_request": gold}))
    (raw / "t.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return raw


def run(tmp_path, raw, session, *extra):
    store = DataStore([], [], [], dict(PLACES), tmp_path / "routes")
    args = brc.make_parser().parse_args(
        ["--raw-dir", str(raw), "--out", str(tmp_path / "routes"), "--osrm-url", OSRM, "--min-interval", "0",
         "--backoff", "0", *extra]
    )
    return brc.build(args, session=session, store=store, sleep=lambda s: None), store


def test_slug_rule_matches_cached_router_lookup(tmp_path):
    assert brc.route_filename("Flagstaff, Az", "Phoenix") == "flagstaff-az__phoenix.json"
    assert brc.route_filename("O'Hare International Airport, Chicago, Il", "St. Louis, Mo") == (
        "o-hare-international-airport-chicago-il__st-louis-mo.json"
    )
    # a file written under that name is exactly what CachedRouter reads for the geocoded places
    summary, store = run(tmp_path, items_dir(tmp_path, ("Flagstaff, AZ", "Phoenix, AZ")), FakeOSRM())
    router = CachedRouter(store, {"placeholder_detour": {}})
    for o, d in (("Flagstaff, AZ", "Phoenix, AZ"), ("flagstaff", "phoenix"), ("Flagstaff", "Phoenix, AZ")):
        route = router.route(store.geocode(o), store.geocode(d))
        assert route.distance_km == pytest.approx(233.0)


def test_alias_copies_one_request_per_pair(tmp_path):
    session = FakeOSRM()
    raw = items_dir(tmp_path, ("Flagstaff, AZ", "Phoenix, AZ"), ("Flagstaff", "Phoenix"), ("Page, AZ", "Phoenix, AZ"))
    summary, _ = run(tmp_path, raw, session)
    assert summary["pairs_total"] == 2 and len(session.calls) == 2  # items 1 and 2 are the same pair
    built = {tuple(b["pair"]): b["files"] for b in summary["built"]}
    assert sorted(built[("Flagstaff, Az", "Phoenix, Az")]) == [
        "flagstaff-az__phoenix-az.json",
        "flagstaff-az__phoenix.json",
        "flagstaff__phoenix-az.json",
        "flagstaff__phoenix.json",
    ]
    assert built[("Page, Az", "Phoenix, Az")] == ["page-az__phoenix-az.json", "page-az__phoenix.json"]
    files = {p.name: p.read_bytes() for p in (tmp_path / "routes").glob("*.json")}
    assert len(files) == 6 and len({files[n] for n in built[("Flagstaff, Az", "Phoenix, Az")]}) == 1  # same route


def test_canonical_only_writes_the_gold_form(tmp_path):
    summary, _ = run(tmp_path, items_dir(tmp_path, ("Flagstaff, AZ", "Phoenix, AZ")), FakeOSRM(), "--canonical-only")
    assert summary["built"][0]["files"] == ["flagstaff-az__phoenix-az.json"]
    assert [p.name for p in (tmp_path / "routes").glob("*.json")] == ["flagstaff-az__phoenix-az.json"]


def test_skips_existing_and_fills_missing_aliases_without_network(tmp_path):
    raw = items_dir(tmp_path, ("Flagstaff, AZ", "Phoenix, AZ"))
    run(tmp_path, raw, FakeOSRM())
    routes = tmp_path / "routes"
    original = (routes / "flagstaff-az__phoenix-az.json").read_bytes()
    (routes / "flagstaff__phoenix.json").unlink()

    session = FakeOSRM()
    summary, _ = run(tmp_path, raw, session)
    assert session.calls == [] and summary["built"] == []
    assert summary["skipped_existing"][0]["filled"] == ["flagstaff__phoenix.json"]
    assert (routes / "flagstaff__phoenix.json").read_bytes() == original  # copied, not re-fetched
    assert (routes / "flagstaff-az__phoenix-az.json").read_bytes() == original  # never overwritten


def test_route_contract_and_provenance(tmp_path):
    run(tmp_path, items_dir(tmp_path, ("Flagstaff, AZ", "Phoenix, AZ")), FakeOSRM())
    payload = json.loads((tmp_path / "routes/flagstaff-az__phoenix-az.json").read_text(encoding="utf-8"))
    meta = payload.pop("_meta")
    route = Route.model_validate(payload)  # follows the Route contract
    assert (route.distance_km, route.duration_min) == (233.0, 140.0)
    assert (route.origin.lat, route.origin.lon) == FLAGSTAFF
    assert (route.destination.lat, route.destination.lon) == PHOENIX
    prov = route.provenance
    assert prov.source == "OSRM" and prov.status.value == "cached" and prov.retrieved_at.tzinfo is not None
    assert prov.source_url.startswith(f"{OSRM}/route/v1/driving/-111.651300,35.198300;-112.074000,33.448400")
    assert meta == {"origin_text": "Flagstaff, Az", "destination_text": "Phoenix, Az", "benchmark_items": ["t-001"],
                    "osrm_server": OSRM, "built_by": "scripts/build_route_cache.py"}


def test_unresolved_missing_and_failed_pairs_are_reported(tmp_path):
    raw = items_dir(tmp_path, ("Springfield", "Phoenix, AZ"), ("Flagstaff, AZ", None), ("Page, AZ", "Flagstaff, AZ"),
                    ("Phoenix, AZ", "Page, AZ"))
    session = FakeOSRM(script=[503, 200, "NoRoute"])  # pair 1 retried once then ok; pair 2 has no route
    summary, _ = run(tmp_path, raw, session)
    assert {s["item"]: s["reason"] for s in summary["skipped_items"]} == {
        "t-001": "unresolved place: 'Springfield'",
        "t-002": "gold has no destination",
    }
    assert len(session.calls) == 3 and len(summary["built"]) == 1
    assert summary["failed"][0]["pair"] == ["Phoenix, Az", "Page, Az"]
    assert summary["failed"][0]["reason"].startswith("no_route: OSRM route failed with NoRoute")


def test_dry_run_makes_no_requests_and_lists_slugs(tmp_path):
    session = FakeOSRM()
    summary, _ = run(tmp_path, items_dir(tmp_path, ("Page, AZ", "Phoenix, AZ")), session, "--dry-run")
    assert session.calls == [] and not (tmp_path / "routes").exists()
    assert summary["pairs"] == [{"origin": "Page, Az", "destination": "Phoenix, Az", "items": ["t-001"],
                                 "files": ["page-az__phoenix-az.json", "page-az__phoenix.json"]}]


def test_rate_limiter_spaces_requests():
    now, slept = [0.0], []
    def sleep(s):
        slept.append(s)
        now[0] += s

    limiter = brc.RateLimiter(1.0, clock=lambda: now[0], sleep=sleep)
    limiter.wait()
    now[0] += 0.25
    limiter.wait()
    now[0] += 3.0
    limiter.wait()
    assert slept == [pytest.approx(0.75)]


def test_demo_server_refuses_faster_than_one_per_second():
    with pytest.raises(SystemExit):
        brc.main(["--dry-run", "--min-interval", "0.5"])


def test_place_aliases_keep_canonical_first():
    store = DataStore([], [], [], dict(PLACES), Path("."))
    assert brc.aliases(store, store.geocode("Flagstaff, AZ")) == ["Flagstaff, Az", "Flagstaff"]
    assert brc.aliases(store, Place(text="Page, Az", lat=PAGE[0], lon=PAGE[1])) == ["Page, Az"]
