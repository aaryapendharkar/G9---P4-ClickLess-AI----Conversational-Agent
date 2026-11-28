"""Gazetteer lookups accept other spellings of the same place, and nothing else (place_names.place_key)."""

from pathlib import Path

import pytest

from clickless.data.loaders import DataStore
from clickless.data.place_names import place_key

PLACES = {
    "ely, nv": (39.25, -114.89),
    "ely": (39.25, -114.89),
    "farmington, nm": (36.73, -108.22),
    "st. george, ut": (37.10, -113.58),
    "seattle-tacoma international airport, seatac, wa": (47.44, -122.30),
    "washington, dc": (38.90, -77.04),
    "charleston, sc": (32.78, -79.93),
    "charleston, wv": (38.35, -81.63),
}


@pytest.fixture
def store():
    return DataStore([], [], [], dict(PLACES), Path("."))


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("Ely, NV", "ely, nv"),
        ("Ely, Nevada", "ely, nv"),
        ("ely,NV", "ely, nv"),
        ("  ELY ,  nv ", "ely, nv"),
        ("Ely, NV, USA", "ely, nv"),
        ("Ely, Nevada, United States", "ely, nv"),
        ("Farmington, New Mexico", "farmington, nm"),
        ("St George, Utah", "st george, ut"),
        ("St. George, UT", "st george, ut"),
        (
            "Seattle–Tacoma International Airport, SeaTac, Washington",
            "seattle tacoma international airport, seatac, wa",
        ),
        ("Washington, D.C.", "washington, dc"),
        ("Washington, District of Columbia", "washington, dc"),
        ("Charleston, West Virginia", "charleston, wv"),
    ],
)
def test_place_key_normalizes_spelling(text, key):
    assert place_key(text) == key


@pytest.mark.parametrize("text", ["Flagstaff", "New York", "George Washington", "Kansas City", "Springfield"])
def test_place_key_needs_a_comma_before_the_state(text):
    assert place_key(text) == text.lower()


@pytest.mark.parametrize(
    ("text", "canonical"),
    [
        ("Ely, Nevada", "Ely, Nv"),
        ("ELY,NV", "Ely, Nv"),
        ("Ely, NV, USA", "Ely, Nv"),
        ("Farmington, New Mexico", "Farmington, Nm"),
        ("St George, Utah", "St. George, Ut"),
        ("Seattle-Tacoma International Airport, SeaTac, Washington",
         "Seattle-Tacoma International Airport, Seatac, Wa"),
        ("Charleston, West Virginia", "Charleston, Wv"),
    ],
)
def test_other_spellings_resolve_to_the_gazetteer_entry(store, text, canonical):
    place = store.geocode(text)
    key = canonical.lower()
    # The returned text is the gazetteer's own spelling, so CachedRouter finds the cached route file.
    assert place.resolved and place.text == canonical and (place.lat, place.lon) == PLACES[key]


def test_exact_keys_still_win(store):
    assert store.geocode("ely").text == "Ely" and store.geocode("Ely, NV").text == "Ely, Nv"


@pytest.mark.parametrize("text", ["Ely, Utah", "Atlantis, Nevada", "Springfield, Illinois", "Nevada", "Charleston"])
def test_the_gazetteer_stays_closed(store, text):
    """Another state, an unknown place, or a bare name that is not a key stays unresolved."""
    place = store.geocode(text)
    assert not place.resolved and place.text == text


def test_a_spelling_shared_by_two_places_is_never_picked():
    places = {"st. george, ut": (37.1, -113.6), "st george, ut": (40.0, -100.0)}
    store = DataStore([], [], [], places, Path("."))
    assert store.geocode("St. George, UT").resolved  # exact key
    assert not store.geocode("St George, Utah").resolved  # normalized: two candidates, no guess


def test_the_index_follows_a_replaced_gazetteer(store):
    assert store.geocode("Ely, Nevada").resolved
    store.places = {"page, az": (36.91, -111.46)}
    assert not store.geocode("Ely, Nevada").resolved and store.geocode("Page, Arizona").resolved
