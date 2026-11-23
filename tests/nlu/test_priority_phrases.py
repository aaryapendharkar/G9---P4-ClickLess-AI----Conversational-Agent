from clickless.nlu.priority_phrases import (
    detect_priority_keys,
    detect_profile,
    detect_weight_overrides,
)


def test_affirmative_priority_is_detected():
    text = "Food matters more than time, and I want an accessible restroom."

    assert detect_profile(text) == "balanced"
    assert detect_priority_keys(text) == {"food", "restroom_wheelchair"}
    assert detect_weight_overrides(text) == {
        "amenity_value_min.food": 30.0,
        "amenity_value_min.restroom_wheelchair": 25.0,
    }


def test_negated_priority_is_not_detected():
    text = "Do not prioritize food, and I don't need to save time."

    assert detect_profile(text) == "balanced"
    assert detect_priority_keys(text) == set()
    assert detect_weight_overrides(text) == {}


def test_required_amenities_are_important_but_negated_needs_are_not():
    assert detect_priority_keys("I need a wheelchair accessible restroom") == {"restroom"}
    assert detect_priority_keys("I must have a restroom") == {"restroom"}
    assert detect_priority_keys("I don't need a wheelchair accessible restroom") == set()
    assert detect_priority_keys("I don't need a restroom, but I must have lunch") == {"food"}
