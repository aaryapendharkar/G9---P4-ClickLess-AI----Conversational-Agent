"""Category gate shared by every preference matcher (ADR 0013). Owner: Manas.

An amenity can satisfy a preference only if it has the preference's category. Every matcher
(TF-IDF in Config A, Sentence-BERT in Configs B/C) returns 0.0 for an amenity of another
category, whatever the text similarity, and checks the gate before it does any work. Text
similarity only ranks amenities inside the requested category.

Why a gate and not a score threshold:
  * Planning, ranking and the explanation all key preferences by category ('food',
    'restroom_wheelchair'), and planning lists every amenity with a score > 0 under
    "Nearby (requested)". So "satisfied" must mean "an amenity of that category".
  * Raw Sentence-BERT scores of the two kinds overlap. On the fixtures, Fixture Coffee Bar scores
    0.45 for "restroom", above the 0.36 of Verde Fixture Kitchen for "lunch". No threshold keeps
    the second and drops the first, and a threshold tuned for one model would not fit TF-IDF.
"""

from __future__ import annotations

from ..contracts import Amenity, AmenityPreference


def passes_category_gate(preference: AmenityPreference, amenity: Amenity) -> bool:
    """True if `amenity` may satisfy `preference`, i.e. it has the same `AmenityCategory`."""
    return amenity.category == preference.category
