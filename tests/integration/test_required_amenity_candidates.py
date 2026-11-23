"""#133 / #136 on the real snapshot: demo scenario 4 keeps its accessible-restroom station.

Flagstaff -> Page in a Kia EV6 with "I need a wheelchair accessible restroom": the only stop with a
wheelchair=yes restroom (Flagstaff Chevrolet, next to osm:way/295598500) is a slower charger, so the
2-per-stretch cap alone drops it before planning. It must be retained when the restroom is required,
and a nice_to_have restroom must leave the candidates exactly as they were.
Needs the downloaded snapshot with its route cache; skipped otherwise (like the other snapshot tests).
"""

import pytest

from clickless.config import load_config, repo_path
from clickless.contracts import AmenityCategory, AmenityPreference, TripRequest
from clickless.data import DataStore
from clickless.pipeline import build_pipeline

SNAPSHOT = repo_path("data/snapshot")
needs_snapshot = pytest.mark.skipif(
    not (SNAPSHOT / "stations.json").is_file() or not (SNAPSHOT / "routes").is_dir(),
    reason="needs the downloaded snapshot with its route cache (#36, #92)",
)
ACCESSIBLE_RESTROOM = "osm:way/295598500"


@pytest.fixture(scope="module")
def snapshot_pipe():
    return build_pipeline(load_config("C"), DataStore.from_dir(SNAPSHOT))


def _scenario_4(store, priority):
    return TripRequest(
        request_id="scenario-4",
        raw_text=(
            "Flagstaff to Page in my Kia EV6, 60% battery, arrive with at least 20%, "
            "I need a wheelchair accessible restroom at the stop"
        ),
        origin=store.geocode("Flagstaff, AZ"),
        destination=store.geocode("Page, AZ"),
        vehicle_id="kia-ev6-lr-rwd-2024",
        start_soc_pct=60,
        arrival_soc_pct_min=20,
        amenities=[
            AmenityPreference(
                category=AmenityCategory.RESTROOM,
                phrase="wheelchair accessible restroom",
                wheelchair_required=True,
                priority=priority,
            )
        ],
    )


def _usable_with_restroom(pipe, req, cands):
    """Stations the EV6 can actually stop at that have the accessible restroom nearby. (Unusable
    stations next to it can fill leftover slots, but the planner can never pick them.)"""
    return [
        c.station.station_id
        for c in cands
        if pipe._station_usable(c.station, req)
        and any(n.amenity.amenity_id == ACCESSIBLE_RESTROOM for n in c.amenities)
    ]


@needs_snapshot
def test_scenario_4_keeps_the_accessible_restroom_station(snapshot_pipe):
    req = _scenario_4(snapshot_pipe.store, "important")
    route = snapshot_pipe.router.route(req.origin, req.destination)
    retriever = snapshot_pipe.retriever
    assert retriever.required_extra_per_bin == 2
    # Flagstaff Chevrolet (afdc:330584), retained on top of the k=2 picks
    assert _usable_with_restroom(snapshot_pipe, req, retriever.candidates(route, req)) == ["afdc:330584"]

    retriever.required_extra_per_bin = None
    try:
        assert _usable_with_restroom(snapshot_pipe, req, retriever.candidates(route, req)) == []  # cut (the bug)
    finally:
        retriever.required_extra_per_bin = 2


@needs_snapshot
def test_a_nice_to_have_restroom_leaves_the_candidates_unchanged(snapshot_pipe):
    req = _scenario_4(snapshot_pipe.store, "nice_to_have")
    route = snapshot_pipe.router.route(req.origin, req.destination)
    retriever = snapshot_pipe.retriever
    with_retention = [c.model_dump(mode="json") for c in retriever.candidates(route, req)]
    retriever.required_extra_per_bin = None
    try:
        without = [c.model_dump(mode="json") for c in retriever.candidates(route, req)]
    finally:
        retriever.required_extra_per_bin = 2
    assert with_retention == without
