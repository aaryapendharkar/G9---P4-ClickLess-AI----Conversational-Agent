"""#137: a requested amenity with no OSM name is shown by its category, and the
"Nearby (requested):" line is never printed empty.

Snapshot cases: deepti-028 (an unnamed wheelchair-accessible restroom, osm:node/11333407713, was
dropped behind eight named cafés) and deepti-025 (the only match, osm:way/295598500, is unnamed, so
the line printed as "Nearby (requested): " with nothing after it). The fixture demo stop has the
same shape: fix:a05 (food) and fix:a06 (restroom, wheelchair=yes).
"""

import copy

import pytest

from clickless.contracts import Evidence, PlanResult
from clickless.explain.grounded import GroundedLLMExplainer
from clickless.explain.templates import TemplateExplainer, amenity_label, requested_amenity_labels
from evaluation.metrics.grounding import check_claim

NEARBY = "  Nearby (requested):"


def _store_with(store, **changes_by_id):
    """A copy of the fixture store whose amenities have the given fields changed."""
    changed = copy.copy(store)
    changed.amenities = [
        amenity.model_copy(update=changes_by_id[amenity.amenity_id]) if amenity.amenity_id in changes_by_id else amenity
        for amenity in store.amenities
    ]
    return changed


def _nearby_lines(text):
    return [line for line in text.splitlines() if line.startswith(NEARBY)]


@pytest.fixture
def demo_result(pipe_a, demo_text):
    result = pipe_a.run(demo_text, request_id="unnamed")
    assert result.alternatives[0].itinerary.stops[0].amenity_ids == ["fix:a05", "fix:a06"]
    return result


def test_an_unnamed_accessible_restroom_is_shown_by_category(store, demo_result):
    # deepti-028's case: the requested restroom has no name, but it is what satisfies the request
    explainer = TemplateExplainer(_store_with(store, **{"fix:a06": {"name": None}}))
    text, _ = explainer.explain(demo_result)
    assert _nearby_lines(text) == [f"{NEARBY} Verde Fixture Kitchen, an accessible restroom"]


def test_the_only_matched_amenity_unnamed_still_fills_the_line(store, demo_result):
    # deepti-025's case: every matched amenity is unnamed, which used to print an empty line
    explainer = TemplateExplainer(_store_with(store, **{"fix:a05": {"name": None}, "fix:a06": {"name": None}}))
    text, _ = explainer.explain(demo_result)
    assert _nearby_lines(text) == [f"{NEARBY} an accessible place to eat, an accessible restroom"]


def test_the_line_is_omitted_when_there_is_nothing_to_show(store, demo_result):
    stop = demo_result.alternatives[0].itinerary.stops[0]
    for amenity_ids in ([], ["osm:node/not-in-this-store"]):
        result = demo_result.model_copy(deep=True)
        result.alternatives[0].itinerary.stops[0] = stop.model_copy(update={"amenity_ids": amenity_ids})
        text, _ = TemplateExplainer(store).explain(result)
        assert "Nearby" not in text


def test_amenities_follow_the_order_the_driver_asked_for(store, demo_result):
    # A requested restroom must not end up behind a list of cafés (deepti-028 had eight of them).
    result = demo_result.model_copy(deep=True)
    result.request.amenities.reverse()  # restroom first, then food
    explainer = TemplateExplainer(_store_with(store, **{"fix:a06": {"name": None}}))
    text, _ = explainer.explain(result)
    assert _nearby_lines(text) == [f"{NEARBY} an accessible restroom, Verde Fixture Kitchen"]


def test_accessible_is_only_said_for_wheelchair_yes(store):
    restroom = next(a for a in store.amenities if a.amenity_id == "fix:a06")
    labels = {
        wheelchair: amenity_label(restroom.model_copy(update={"name": None, "wheelchair": wheelchair}))
        for wheelchair in ("yes", "limited", "no", "unknown")
    }
    assert labels == {
        "yes": "an accessible restroom",
        "limited": "a restroom",
        "no": "a restroom",
        "unknown": "a restroom",
    }
    assert amenity_label(restroom) == "Verde Fixture Restroom"  # a name always wins


def test_identical_unnamed_amenities_are_listed_once(store):
    restroom = next(a for a in store.amenities if a.amenity_id == "fix:a06").model_copy(update={"name": None})
    twins = {amenity_id: restroom.model_copy(update={"amenity_id": amenity_id}) for amenity_id in ("r1", "r2")}
    assert [label for label, _ in requested_amenity_labels(["r1", "r2"], twins, None)] == ["an accessible restroom"]


def test_saved_results_keep_every_amenity(store, demo_result):
    # Display changes only: the stop keeps all its amenity ids, and so does the JSON round trip.
    TemplateExplainer(_store_with(store, **{"fix:a06": {"name": None}})).explain(demo_result)
    restored = PlanResult.model_validate_json(demo_result.model_dump_json())
    assert restored.alternatives[0].itinerary.stops[0].amenity_ids == ["fix:a05", "fix:a06"]


def _fact(amenity, field, value):
    return Evidence(
        evidence_id=f"kg:{amenity.amenity_id}:{field}",
        subject_id=amenity.amenity_id,
        field=field,
        value=value,
        provenance=amenity.provenance,
    )


def _nearby_claims(claims):
    return [claim for claim in claims if claim.text.startswith("Nearby (requested):")]


def test_config_c_cites_category_and_wheelchair_for_an_unnamed_restroom(store, demo_result):
    unnamed = _store_with(store, **{"fix:a06": {"name": None}})
    kitchen, restroom = (a for a in unnamed.amenities if a.amenity_id in ("fix:a05", "fix:a06"))
    result = demo_result.model_copy(
        update={
            "evidence": [
                _fact(kitchen, "name", kitchen.name),
                _fact(restroom, "name", None),
                _fact(restroom, "category", "restroom"),
                _fact(restroom, "wheelchair", "yes"),
            ]
        }
    )
    text, claims = GroundedLLMExplainer(unnamed, {}).explain(result)

    assert _nearby_lines(text) == [f"{NEARBY} Verde Fixture Kitchen, an accessible restroom"]
    [claim] = _nearby_claims(claims)
    assert claim.text == "Nearby (requested): Verde Fixture Kitchen, an accessible restroom."
    assert claim.supported_by == [
        "kg:fix:a05:name",
        "kg:fix:a06:category",
        "kg:fix:a06:wheelchair",
    ]
    assert check_claim(claim, result) is None  # passes the grounding metric's wheelchair rule


def test_config_c_leaves_out_an_unnamed_amenity_its_evidence_does_not_back(store, demo_result):
    unnamed = _store_with(store, **{"fix:a06": {"name": None}})
    kitchen, restroom = (a for a in unnamed.amenities if a.amenity_id in ("fix:a05", "fix:a06"))
    # the KG says wheelchair=unknown, so "an accessible restroom" is not backed
    result = demo_result.model_copy(
        update={
            "evidence": [
                _fact(kitchen, "name", kitchen.name),
                _fact(restroom, "category", "restroom"),
                _fact(restroom, "wheelchair", "unknown"),
            ]
        }
    )
    _, claims = GroundedLLMExplainer(unnamed, {}).explain(result)
    [claim] = _nearby_claims(claims)
    assert claim.text == "Nearby (requested): Verde Fixture Kitchen."
    assert claim.supported_by == ["kg:fix:a05:name"]
