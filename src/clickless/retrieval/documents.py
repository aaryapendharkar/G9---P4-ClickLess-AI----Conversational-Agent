"""Text that preference matchers compare. Owner: Manas.

TF-IDF (Config A) and Sentence-BERT (Configs B/C) must read the same amenity documents and the
same query text so the component-level comparison is fair (proposal §IV-A).

Only descriptive fields go into a document. Accessibility and access tags are left out on
purpose: planning applies the wheelchair rule (unknown never counts), and a matcher must not add
its own fuzzy version of it.
"""

from __future__ import annotations

from ..contracts import Amenity, AmenityCategory, AmenityPreference

# OSM tags kept by data/osm_amenities.py that describe what a place is (ADR 0011).
DOCUMENT_TAGS: tuple[str, ...] = ("amenity", "shop", "tourism", "cuisine", "brand")


def _readable(value: str) -> str:
    """OSM value -> words: 'fast_food' -> 'fast food', 'mexican;tacos' -> 'mexican, tacos'."""
    parts = (part.strip().replace("_", " ") for part in value.split(";"))
    return ", ".join(part for part in parts if part)


def amenity_document(amenity: Amenity) -> str:
    """Name, category, then descriptive OSM tags, e.g. 'Verde Fixture Kitchen. food. restaurant. mexican'.

    Repeated values (a brand equal to the name, `shop=coffee` in the coffee category) appear once.
    """
    parts = [amenity.name or "", amenity.category.value]
    parts += [_readable(amenity.tags.get(key, "")) for key in DOCUMENT_TAGS]
    seen: set[str] = set()
    kept: list[str] = []
    for part in (p.strip() for p in parts):
        if part and part.lower() not in seen:
            seen.add(part.lower())
            kept.append(part)
    return ". ".join(kept)


def preference_query(preference: AmenityPreference) -> str:
    """The driver's own phrase plus the category word when the phrase lacks it ('lunch' -> 'lunch food').

    An empty phrase falls back to the category word. 'other' is never appended: it carries no meaning.
    """
    phrase = " ".join(preference.phrase.split())
    category = preference.category.value
    if not phrase:
        return category
    if preference.category is AmenityCategory.OTHER or category in phrase.lower().split():
        return phrase
    return f"{phrase} {category}"
