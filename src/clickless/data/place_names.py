"""Generic spelling normalization for gazetteer lookups (DataStore.geocode). Owner: Aarya.

The gazetteer keys are the benchmark spellings, lower-cased ("ely, nv"). A parser may return the same
place spelled another way ("Ely, Nevada", "Ely,NV", "ELY ,  NV", "Ely, NV, USA"). `place_key` maps every
spelling to one comparison key, and DataStore.geocode compares those keys when the exact text is not a
gazetteer key.

Only spelling is normalized: case, spacing, punctuation, a trailing country, and a US state written
out in full or as its postal code. No place names, landmarks or aliases are added here (that would
tune the gazetteer to the benchmark), and the gazetteer stays closed: a place that is not in it is
still unresolved.
"""

from __future__ import annotations

import re

STATE_CODES: dict[str, str] = {
    "alabama": "al", "alaska": "ak", "arizona": "az", "arkansas": "ar", "california": "ca",
    "colorado": "co", "connecticut": "ct", "delaware": "de", "district of columbia": "dc",
    "florida": "fl", "georgia": "ga", "hawaii": "hi", "idaho": "id", "illinois": "il",
    "indiana": "in", "iowa": "ia", "kansas": "ks", "kentucky": "ky", "louisiana": "la",
    "maine": "me", "maryland": "md", "massachusetts": "ma", "michigan": "mi", "minnesota": "mn",
    "mississippi": "ms", "missouri": "mo", "montana": "mt", "nebraska": "ne", "nevada": "nv",
    "new hampshire": "nh", "new jersey": "nj", "new mexico": "nm", "new york": "ny",
    "north carolina": "nc", "north dakota": "nd", "ohio": "oh", "oklahoma": "ok", "oregon": "or",
    "pennsylvania": "pa", "puerto rico": "pr", "rhode island": "ri", "south carolina": "sc",
    "south dakota": "sd", "tennessee": "tn", "texas": "tx", "utah": "ut", "vermont": "vt",
    "virginia": "va", "washington": "wa", "west virginia": "wv", "wisconsin": "wi", "wyoming": "wy",
}
_CODES = frozenset(STATE_CODES.values())
# Longest names first, so "west virginia" wins over "virginia".
_STATE_NAME_AT_END = re.compile(
    r"^(?P<head>.+?), (?P<state>"
    + "|".join(re.escape(n) for n in sorted(STATE_CODES, key=len, reverse=True))
    + r")$"
)
_STATE_CODE_AT_END = re.compile(r"^(?P<head>.+?), (?P<state>[a-z]{2})$")
_COUNTRY_AT_END = re.compile(r", (?:usa|u s a|us|u s|united states(?: of america)?)$")
_PUNCT = re.compile(r"[^a-z0-9, ]+")


def place_key(text: str) -> str:
    """One comparison key per spelling: "Ely, Nevada" / "ely,NV" / "Ely, NV, USA" -> "ely, nv".

    The state rules need a comma before the state, so a bare place ("Flagstaff"), a bare state name
    ("New York") or a name that merely ends in a state ("George Washington") keeps its words.
    """
    s = text.strip().lower().replace("&", " and ")
    s = _PUNCT.sub(" ", s)  # "st. george" -> "st  george", "d.c." -> "d c "
    s = re.sub(r"\s*,\s*", ", ", s)
    s = re.sub(r"\s+", " ", s).strip(" ,")
    s = re.sub(r"\bd c$", "dc", s)
    s = _COUNTRY_AT_END.sub("", s).strip(" ,")
    m = _STATE_NAME_AT_END.match(s)
    if m:
        return f"{m['head'].strip(' ,')}, {STATE_CODES[m['state']]}"
    m = _STATE_CODE_AT_END.match(s)
    if m and m["state"] in _CODES:
        return f"{m['head'].strip(' ,')}, {m['state']}"
    return s
