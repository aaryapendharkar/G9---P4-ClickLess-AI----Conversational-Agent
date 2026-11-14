"""DataStore lookups a clarification answer is merged with (issue #53, ADR 0014). Owner: Aarya."""

import pytest

from clickless.data import DataStore
from clickless.data.vehicles import load_curated_vehicles


@pytest.fixture(scope="module")
def curated(tmp_path_factory):
    """Store holding only the 15 curated vehicles (no stations needed for these lookups)."""
    return DataStore([], load_curated_vehicles(), [], {}, tmp_path_factory.mktemp("routes"))


def test_vehicle_label_is_driver_facing(curated, store):
    assert curated.vehicle_label("tesla-model-3-rwd-2024") == "2024 Tesla Model 3 RWD"
    assert curated.vehicle_label("chevrolet-bolt-euv-2023") == "2023 Chevrolet Bolt EUV"  # no variant
    assert store.vehicle_label("fixture-spark-lr-2025") == "2025 FixtureMotors Spark Long Range"
    with pytest.raises(KeyError):
        curated.vehicle_label("not-a-vehicle")


def test_every_curated_vehicle_has_a_distinct_label(curated):
    labels = [curated.vehicle_label(v) for v in curated.vehicles]
    assert len(set(labels)) == len(labels) == 15


def test_ambiguous_model_3_is_resolved_by_answer_within_options(curated):
    options = [v.vehicle_id for v in curated.find_vehicles("my Model 3")]
    assert sorted(options) == ["tesla-model-3-lr-awd-2024", "tesla-model-3-rwd-2024"]

    picked = curated.find_vehicles("the Model 3 RWD", among=options)
    assert [v.vehicle_id for v in picked] == ["tesla-model-3-rwd-2024"]

    picked = curated.find_vehicles("the Model 3 Long Range", among=options)
    assert [v.vehicle_id for v in picked] == ["tesla-model-3-lr-awd-2024"]


@pytest.mark.parametrize(
    "answer, expected",
    [
        ("the RWD one", "tesla-model-3-rwd-2024"),
        ("rwd", "tesla-model-3-rwd-2024"),
        ("long range", "tesla-model-3-lr-awd-2024"),
        ("The AWD", "tesla-model-3-lr-awd-2024"),
        ("my Tesla Model 3, the long range", "tesla-model-3-lr-awd-2024"),  # alias tie broken by "long range"
        ("no, the RWD", "tesla-model-3-rwd-2024"),
    ],
)
def test_short_answer_picks_the_option_it_names(curated, answer, expected):
    options = ["tesla-model-3-lr-awd-2024", "tesla-model-3-rwd-2024"]
    assert [v.vehicle_id for v in curated.find_vehicles(answer, among=options)] == [expected]


@pytest.mark.parametrize("answer", ["model 3", "2024", "rwd or awd, not sure", "not the RWD one", "the red one"])
def test_unclear_answer_picks_nothing(curated, answer):
    options = ["tesla-model-3-lr-awd-2024", "tesla-model-3-rwd-2024"]
    picked = [v.vehicle_id for v in curated.find_vehicles(answer, among=options)]
    assert len(picked) != 1, picked  # the caller re-asks; it never guesses


def test_short_answers_do_not_match_without_options(curated):
    assert curated.find_vehicles("the RWD one") == []  # free text keeps the strict alias behaviour


def test_answer_cannot_switch_to_a_vehicle_that_was_not_offered(curated):
    options = ["tesla-model-3-lr-awd-2024", "tesla-model-3-rwd-2024"]
    assert curated.find_vehicles("actually an Ioniq 5", among=options) == []
    assert [v.vehicle_id for v in curated.find_vehicles("actually an Ioniq 5")] == ["hyundai-ioniq-5-lr-rwd-2024"]


def test_among_ignores_unknown_ids_and_empty_list_matches_nothing(curated):
    assert [v.vehicle_id for v in curated.find_vehicles("model 3 rwd", among=["tesla-model-3-rwd-2024", "x"])] == [
        "tesla-model-3-rwd-2024"
    ]
    assert curated.find_vehicles("model 3 rwd", among=[]) == []


def test_find_vehicles_default_behaviour_unchanged(store):
    assert [v.vehicle_id for v in store.find_vehicles("in my spark lr")] == ["fixture-spark-lr-2025"]
    assert store.find_vehicles("a horse") == []


def test_geocode_answer_never_guesses(store):
    assert store.geocode("  Flagstaff ").resolved  # answers are trimmed, case-insensitive
    unresolved = store.geocode("Springfield")
    assert not unresolved.resolved and unresolved.lat is None and unresolved.text == "Springfield"
