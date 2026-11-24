"""Fixture scenarios WITHOUT a required accessible restroom, for the #162 no-change guard.

`tests/evaluation/data/oracle_no_access_rule_golden.json` holds the oracle's answers to these
scenarios recorded on main BEFORE the accessibility rule existed (commit 4e739b5). The rule must
not change any of them. Regenerate only for a deliberate model change:
    PYTHONPATH=src python tests/evaluation/_access_scenarios.py

The worlds carry restroom amenities (wheelchair yes / limited / unknown) next to the stations, so
a rule that leaked into requests without `important` + `wheelchair_required` would show up here.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from _oracle_world import ENERGY, KM_PER_DEG, PROV, TwinOracle, from_plan, itinerary, req, route, station, stop, vehicle

from clickless.contracts import Amenity, AmenityPreference
from clickless.data.loaders import DataStore
from evaluation.oracle.label_setting import LabelSettingOracle, OracleConfig

GOLDEN = Path(__file__).parent / "data" / "oracle_no_access_rule_golden.json"


def amenity(aid, km, off_m=0.0, category="restroom", wheelchair="yes") -> Amenity:
    return Amenity(
        amenity_id=aid,
        category=category,
        lat=(off_m / 1000) / KM_PER_DEG,
        lon=km / KM_PER_DEG,
        wheelchair=wheelchair,
        provenance=PROV,
    )


STATIONS = (
    station("s60", 60),
    station("s100", 100, 0.5),
    station("s120", 120, 1.0),
    station("s150", 150),
)
AMENITIES = (
    amenity("r-yes-100", 100, 300),  # within 400 m of s100 (0.5 km off-route, amenity 0.3 km)
    amenity("r-limited-60", 60, 100, wheelchair="limited"),
    amenity("r-unknown-150", 150, 50, wheelchair="unknown"),
    amenity("f-yes-120", 120, 1000, category="food"),
)


def oracle(**cfg) -> TwinOracle:
    r = route(200.0)
    store = DataStore(list(STATIONS), [vehicle()], list(AMENITIES), {}, Path("."))

    def make(method):
        return LabelSettingOracle(store, OracleConfig(energy=ENERGY, method=method, **cfg), route_source=lambda o, d: r)

    return TwinOracle(make("indexed"), make("exhaustive"))


def pref(category="restroom", wheelchair=False, priority="nice_to_have") -> AmenityPreference:
    return AmenityPreference(category=category, wheelchair_required=wheelchair, priority=priority)


REQUESTS = {
    "no_amenities": {},
    "restroom_nice_wheelchair": {"amenities": [pref(wheelchair=True)]},
    "restroom_important_no_wheelchair": {"amenities": [pref(priority="important")]},
    "food_important_wheelchair": {"amenities": [pref("food", True, "important")]},
    "mixed_soft": {"amenities": [pref(wheelchair=True), pref("food", priority="important")]},
    "detour_limit": {"amenities": [pref(wheelchair=True)], "max_detour_min": 3.0},
    "arrival_30": {"amenities": [pref(wheelchair=True)], "arrival_soc_pct_min": 30},
    "direct_drive": {"start": 95.0, "amenities": [pref(wheelchair=True)]},
    "energy_gap": {"start": 12.0, "amenities": [pref(wheelchair=True)]},
}
SCOPES = ("stop", "total")


def _plain(obj):
    return json.loads(
        json.dumps(
            dataclasses.asdict(obj),
            default=lambda o: o.model_dump(mode="json") if hasattr(o, "model_dump") else str(o),
        )
    )


def run_all() -> dict:
    out = {}
    for scope in SCOPES:
        o = oracle(detour_scope=scope)
        for name, kw in REQUESTS.items():
            kw = dict(kw)
            request = req(start=kw.pop("start", 50.0), **kw)
            front = o.pareto_plans(request)
            hand = [
                itinerary(),
                itinerary(stop("s60", km=60, arrive=26.0, depart=80.0)),
                itinerary(stop("s100", km=100, arrive=10.0, depart=90.0)),
                itinerary(stop("s60", km=60, arrive=26.0, depart=60.0), stop("s150", km=150, arrive=24.0, depart=60.0)),
            ]
            checks = [o.check_itinerary(request, from_plan(p)) for p in front.plans] + [
                o.check_itinerary(request, it) for it in hand
            ]
            out[f"{scope}/{name}"] = {
                "is_feasible": _plain(o.is_feasible(request)),
                "pareto_plans": _plain(front),
                "check_itinerary": [_plain(c) for c in checks],
            }
    return out


if __name__ == "__main__":
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(json.dumps(run_all(), indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {GOLDEN}")
