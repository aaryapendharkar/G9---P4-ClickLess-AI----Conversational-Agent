"""#106 / #105 on the real snapshot: long routes from a dense origin get a verified plan.

Before #106 the 60-candidate cap was filled by stations within ~2 km of the origin, so these
requests reached the planner without a single later-route station and came back infeasible.
Needs the downloaded snapshot with its route cache; skipped otherwise (like the other snapshot tests).
"""

import json
import math

import pytest

from clickless.config import load_config, repo_path
from clickless.contracts import AmenityCategory, AmenityPreference, PlanResult, TripRequest
from clickless.data import DataStore
from clickless.pipeline import build_pipeline

SNAPSHOT = repo_path("data/snapshot")
needs_snapshot = pytest.mark.skipif(
    not (SNAPSHOT / "stations.json").is_file() or not (SNAPSHOT / "routes").is_dir(),
    reason="needs the downloaded snapshot with its route cache (#36, #92)",
)


@pytest.fixture(scope="module")
def snapshot_pipe():
    return build_pipeline(load_config("A"), DataStore.from_dir(SNAPSHOT))


def _dev_request(store, item_id):
    line = next(
        ln
        for ln in repo_path("benchmark/requests/dev/dev.jsonl").read_text(encoding="utf-8").splitlines()
        if f'"id": "{item_id}"' in ln or f'"id":"{item_id}"' in ln
    )
    gold = dict(json.loads(line)["gold_request"])
    gold["origin"], gold["destination"] = store.geocode(gold["origin"]), store.geocode(gold["destination"])
    return TripRequest.model_validate({"request_id": item_id, "raw_text": item_id, **gold})


def _plan(pipe, req):
    return pipe.plan_request(req, PlanResult(request_id=req.request_id, config="A", status="ok", request=req))


@needs_snapshot
@pytest.mark.parametrize("item_id", ["deepti-014", "deepti-015"])
def test_issue_105_items_get_a_verified_plan(snapshot_pipe, item_id):
    req = _dev_request(snapshot_pipe.store, item_id)
    route = snapshot_pipe.router.route(req.origin, req.destination)
    cands = snapshot_pipe.retriever.candidates(route, req)
    # 60 under the cap, plus at most 2 per ~25 km stretch kept for a required amenity (#133 / #136):
    # deepti-015's gold request marks lunch important
    assert 60 <= len(cands) <= 60 + 2 * math.ceil(route.distance_km / 25)
    assert max(c.along_route_km for c in cands) > 0.8 * route.distance_km  # before #106: all within 2.1 km
    result = _plan(snapshot_pipe, req)
    assert result.status == "ok" and result.alternatives
    assert result.verification is not None and result.verification.ok  # the hard-constraint verifier, unchanged


@needs_snapshot
def test_los_angeles_to_san_francisco_demo_gets_a_verified_plan(snapshot_pipe):
    store = snapshot_pipe.store
    req = TripRequest(
        request_id="la-sf",
        raw_text="LA to SF",
        origin=store.geocode("Los Angeles, CA"),
        destination=store.geocode("San Francisco, CA"),
        vehicle_id="chevrolet-bolt-euv-2023",
        start_soc_pct=90,
        arrival_soc_pct_min=15,
        amenities=[AmenityPreference(category=AmenityCategory.FOOD, phrase="lunch stop")],
    )
    result = _plan(snapshot_pipe, req)
    assert result.status == "ok" and result.verification is not None and result.verification.ok
    assert len(result.alternatives[0].itinerary.stops) >= 2  # a 612 km trip in a Bolt is multi-stop
