"""Snapshot validator. LEARNING SWAP (Ian) inside Aarya's module -- reviewed by Aarya.

Checks every record of a DataStore before the freeze:
  * coordinates inside the US bounding boxes; no duplicate ids
  * every station has >= 1 port with a normalized connector; reported DC power must be > 0
  * provenance completeness: source, retrieved_at always; source_updated_at reported as a share
  * vehicles: every curated numeric field has a field_provenance entry
Output feeds the "provenance completeness" metric (proposal §V-C).

Notes on scope:
  * DataStore now rejects duplicate station/vehicle ids when it is built (ADR 0010), so here the
    check catches key/id mismatches and duplicate amenity ids.
  * `ChargingPort.power_kw is None` is the contract's "not reported by the source" flag (ADR
    0010). A DC port with unknown power is counted under the WARNING check `dc_power_unknown`
    (the planner assumes a conservative power for it); it does not block a snapshot.
    `blocking_problems()` returns only the checks that must be empty before a freeze.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from ..contracts import ChargerLevel, Connector, Provenance
from .loaders import DataStore

# (name, lat_min, lat_max, lon_min, lon_max). Generous boxes: the goal is to catch swapped or
# sign-flipped coordinates and (0, 0) placeholders, not to test exact borders.
US_BOUNDING_BOXES: tuple[tuple[str, float, float, float, float], ...] = (
    ("contiguous", 24.0, 49.5, -125.0, -66.5),
    ("alaska", 51.0, 71.5, -180.0, -129.0),
    ("hawaii", 18.5, 22.5, -160.5, -154.5),
    ("puerto_rico_vi", 17.5, 18.6, -67.5, -64.5),
)

# Vehicle fields taken from curated sources; each needs its own provenance entry when present.
CURATED_VEHICLE_FIELDS: tuple[str, ...] = ("usable_kwh", "efficiency_kwh_per_km", "max_dc_kw")

CHECKS: tuple[str, ...] = (
    "coordinates",
    "duplicate_ids",
    "station_ports",
    "dc_power",
    "dc_power_unknown",
    "provenance",
    "vehicle_field_provenance",
)
WARNING_CHECKS: frozenset[str] = frozenset({"dc_power_unknown"})


def in_us(lat: float, lon: float) -> bool:
    return any(a <= lat <= b and c <= lon <= d for _, a, b, c, d in US_BOUNDING_BOXES)


def _provenance_problems(subject: str, prov: Provenance | None) -> list[str]:
    if prov is None:
        return [f"{subject}: missing provenance"]
    problems = []
    if not prov.source:
        problems.append(f"{subject}: provenance has no source")
    if prov.retrieved_at is None:
        problems.append(f"{subject}: provenance has no retrieved_at")
    return problems


def _all_provenance(store: DataStore) -> Iterable[tuple[str, Provenance | None]]:
    for sid, st in store.stations.items():
        yield f"station {sid}", st.provenance
    for am in store.amenities:
        yield f"amenity {am.amenity_id}", am.provenance
    for vid, v in store.vehicles.items():
        for field, prov in v.field_provenance.items():
            yield f"vehicle {vid}.{field}", prov


def validate_store(store: DataStore) -> dict[str, list[str]]:
    """Return {check_name: [problem descriptions]}; empty lists mean the check passed."""
    problems: dict[str, list[str]] = {name: [] for name in CHECKS}

    # coordinates
    points = [(f"station {sid}", s.lat, s.lon) for sid, s in store.stations.items()]
    points += [(f"amenity {a.amenity_id}", a.lat, a.lon) for a in store.amenities]
    points += [(f"place {name!r}", lat, lon) for name, (lat, lon) in store.places.items()]
    for subject, lat, lon in points:
        if not in_us(lat, lon):
            problems["coordinates"].append(f"{subject}: ({lat}, {lon}) outside US bounding boxes")

    # duplicate ids
    for key, st in store.stations.items():
        if key != st.station_id:
            problems["duplicate_ids"].append(f"station key {key!r} != station_id {st.station_id!r}")
    for key, v in store.vehicles.items():
        if key != v.vehicle_id:
            problems["duplicate_ids"].append(f"vehicle key {key!r} != vehicle_id {v.vehicle_id!r}")
    for aid, n in Counter(a.amenity_id for a in store.amenities).items():
        if n > 1:
            problems["duplicate_ids"].append(f"amenity id {aid!r} appears {n} times")

    # station ports + DC power
    for sid, st in store.stations.items():
        if not st.ports:
            problems["station_ports"].append(f"station {sid}: no ports")
        for i, port in enumerate(st.ports):
            if not isinstance(port.connector, Connector):
                problems["station_ports"].append(
                    f"station {sid}: ports[{i}] connector {port.connector!r} not normalized"
                )
            if port.count < 1:
                problems["station_ports"].append(f"station {sid}: ports[{i}] count {port.count} < 1")
            if port.level == ChargerLevel.DC_FAST and port.power_kw is None:
                problems["dc_power_unknown"].append(f"station {sid}: DC ports[{i}] has no power_kw (unknown)")
            if port.power_kw is not None and port.power_kw <= 0:
                problems["dc_power"].append(f"station {sid}: ports[{i}] power_kw {port.power_kw} <= 0")

    # provenance (source + retrieved_at always present)
    for subject, prov in _all_provenance(store):
        problems["provenance"].extend(_provenance_problems(subject, prov))

    # vehicle curated numeric fields
    for vid, v in store.vehicles.items():
        for field in CURATED_VEHICLE_FIELDS:
            if getattr(v, field) is not None and field not in v.field_provenance:
                problems["vehicle_field_provenance"].append(f"vehicle {vid}: {field} has no field_provenance")

    return problems


def blocking_problems(problems: dict[str, list[str]]) -> dict[str, list[str]]:
    """The checks that must be empty before a snapshot is frozen (warnings excluded)."""
    return {k: v for k, v in problems.items() if k not in WARNING_CHECKS and v}


def provenance_completeness(store: DataStore) -> dict[str, float]:
    """Shares for the provenance-completeness metric (proposal §V-C). NaN when there are no records."""
    provs = list(_all_provenance(store))
    n = len(provs)
    if n == 0:
        nan = float("nan")
        return {"n_records": 0, "required_fields_share": nan, "source_updated_at_share": nan}
    complete = sum(not _provenance_problems(s, p) for s, p in provs)
    updated = sum(p is not None and p.source_updated_at is not None for _, p in provs)
    return {"n_records": n, "required_fields_share": complete / n, "source_updated_at_share": updated / n}
