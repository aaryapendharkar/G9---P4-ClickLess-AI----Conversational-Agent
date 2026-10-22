import pytest

from clickless.contracts import AmenityCategory, AmenityPreference


def _route_and_req(pipe_a, text):
    req = pipe_a.parser.parse(text, "c1").request
    return pipe_a.router.route(req.origin, req.destination), req


def test_corridor_excludes_far_station(pipe_a, demo_text):
    route, req = _route_and_req(pipe_a, demo_text)
    ids = {c.station.station_id for c in pipe_a.retriever.candidates(route, req)}
    assert "FIX-ST-004" in ids and "FIX-ST-006" not in ids  # Sedona is outside the 8 km buffer


def test_candidates_ordered_along_route(pipe_a, demo_text):
    route, req = _route_and_req(pipe_a, demo_text)
    along = [c.along_route_km for c in pipe_a.retriever.candidates(route, req)]
    assert along == sorted(along)


def test_amenities_linked_within_radius(pipe_a, demo_text):
    route, req = _route_and_req(pipe_a, demo_text)
    cv = next(c for c in pipe_a.retriever.candidates(route, req) if c.station.station_id == "FIX-ST-004")
    assert {"fix:a05", "fix:a06"} <= {na.amenity.amenity_id for na in cv.amenities}
    assert all(na.distance_m <= 400 for na in cv.amenities)


@pytest.mark.xfail(reason="TODO(Manas): real TF-IDF should score related phrasing, not just category")
def test_tfidf_matches_cuisine_phrase(store):
    from clickless.retrieval.tfidf_matcher import TfidfMatcher
    kitchen = next(a for a in store.amenities if a.amenity_id == "fix:a05")
    burgers = next(a for a in store.amenities if a.amenity_id == "fix:a03")
    pref = AmenityPreference(category=AmenityCategory.FOOD, phrase="mexican food")
    m = TfidfMatcher(store)
    assert m.score(pref, kitchen) > m.score(pref, burgers)
