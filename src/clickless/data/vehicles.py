"""Curated vehicle table loader: data/curated/vehicles.csv -> list[Vehicle]. Owner: Aarya.

Rules (ADR 0009, issue #28):
  * every curated value has its own source URL and source type; each becomes one Provenance in
    `Vehicle.field_provenance` (status=STATIC, retrieved_at=accessed_on);
  * source types: MANUFACTURER, FUELECONOMY or THIRD_PARTY (contracts v0.4); FUELECONOMY URLs must
    point at fueleconomy.gov;
  * `usable_kwh_basis` must be "usable": a nominal/gross capacity is never loaded as usable;
  * list columns (connectors, adapters, aliases) are ';'-separated;
  * anything malformed or unknown raises CuratedVehicleError naming the file, line and column.
    Nothing is silently skipped or defaulted.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import repo_path
from ..contracts import Connector, DataStatus, Provenance, Vehicle

DEFAULT_CSV = "data/curated/vehicles.csv"

COLUMNS: tuple[str, ...] = (
    "vehicle_id",
    "make",
    "model",
    "model_year",
    "variant",
    "usable_kwh",
    "usable_kwh_basis",
    "usable_kwh_source_url",
    "usable_kwh_source_type",
    "efficiency_kwh_per_km",
    "efficiency_source_url",
    "efficiency_source_type",
    "native_connectors",
    "adapters",
    "adapter_source_url",
    "adapter_source_type",
    "max_dc_kw",
    "max_dc_source_url",
    "max_dc_source_type",
    "accessed_on",
    "aliases",
)

CURATED_SOURCE_TYPES: frozenset[str] = frozenset({"MANUFACTURER", "FUELECONOMY", "THIRD_PARTY"})
USABLE_KWH_BASES: frozenset[str] = frozenset({"usable"})

# Plausibility ranges: catch unit mistakes (Wh vs kWh, miles vs km), not judge the car.
NUMERIC_RANGES: dict[str, tuple[float, float]] = {
    "usable_kwh": (10.0, 250.0),
    "efficiency_kwh_per_km": (0.08, 0.60),
    "max_dc_kw": (20.0, 1000.0),
}
MODEL_YEAR_RANGE = (2010, 2035)

# Vehicle field -> CSV column holding its source URL / source type.
PROVENANCE_COLUMNS: dict[str, tuple[str, str]] = {
    "usable_kwh": ("usable_kwh_source_url", "usable_kwh_source_type"),
    "efficiency_kwh_per_km": ("efficiency_source_url", "efficiency_source_type"),
    "max_dc_kw": ("max_dc_source_url", "max_dc_source_type"),
    "adapters": ("adapter_source_url", "adapter_source_type"),
}


class CuratedVehicleError(ValueError):
    """A curated vehicle row (or the file) breaks the table rules."""


def load_curated_vehicles(csv_path: str | Path = DEFAULT_CSV) -> list[Vehicle]:
    path = repo_path(csv_path)
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = tuple(reader.fieldnames or ())
        if header != COLUMNS:
            missing = [c for c in COLUMNS if c not in header]
            extra = [c for c in header if c not in COLUMNS]
            raise CuratedVehicleError(
                f"{path}: header mismatch (missing={missing}, unexpected={extra}); "
                f"expected columns in this order: {list(COLUMNS)}"
            )
        vehicles: list[Vehicle] = []
        seen: dict[str, int] = {}
        for row in reader:
            line = reader.line_num
            where = f"{path}:{line}"
            if None in row or any(v is None for v in row.values()):
                raise CuratedVehicleError(f"{where}: wrong number of fields")
            vehicle = _parse_row({k: v.strip() for k, v in row.items()}, where)
            if vehicle.vehicle_id in seen:
                raise CuratedVehicleError(
                    f"{where}: duplicate vehicle_id {vehicle.vehicle_id!r} (first on line {seen[vehicle.vehicle_id]})"
                )
            seen[vehicle.vehicle_id] = line
            vehicles.append(vehicle)
    if not vehicles:
        raise CuratedVehicleError(f"{path}: no vehicle rows")
    return vehicles


# ---------------------------------------------------------------- row parsing


def _parse_row(row: dict[str, str], where: str) -> Vehicle:
    def err(column: str, msg: str) -> CuratedVehicleError:
        return CuratedVehicleError(f"{where}: column {column!r}: {msg}")

    for col in (
        "vehicle_id",
        "make",
        "model",
        "model_year",
        "usable_kwh",
        "efficiency_kwh_per_km",
        "native_connectors",
        "accessed_on",
        "aliases",
    ):
        if not row[col]:
            raise err(col, "required value is empty")

    vid = row["vehicle_id"]
    if vid != vid.lower() or " " in vid:
        raise err("vehicle_id", f"{vid!r} must be lowercase with no spaces")

    try:
        year = int(row["model_year"])
    except ValueError:
        raise err("model_year", f"{row['model_year']!r} is not an integer") from None
    if not MODEL_YEAR_RANGE[0] <= year <= MODEL_YEAR_RANGE[1]:
        raise err("model_year", f"{year} outside {MODEL_YEAR_RANGE}")

    if row["usable_kwh_basis"] not in USABLE_KWH_BASES:
        raise err(
            "usable_kwh_basis",
            f"{row['usable_kwh_basis']!r}: only usable capacity may be loaded "
            f"(allowed: {sorted(USABLE_KWH_BASES)}); never load nominal/gross capacity as usable",
        )

    numbers = {name: _number(row, name, err) for name in NUMERIC_RANGES}  # required ones checked above

    native = _connectors(row["native_connectors"], "native_connectors", err)
    if not native:
        raise err("native_connectors", "at least one connector is required")
    adapters = _connectors(row["adapters"], "adapters", err)
    if overlap := set(native) & set(adapters):
        raise err("adapters", f"{sorted(c.value for c in overlap)} already native")

    aliases = [a for a in row["aliases"].split(";")]
    if any(not a.strip() for a in aliases):
        raise err("aliases", "empty alias (check for ';;' or a trailing ';')")
    aliases = [a.strip() for a in aliases]
    if any(a != a.lower() for a in aliases):
        raise err("aliases", "aliases must be lowercase")
    if len(set(aliases)) != len(aliases):
        raise err("aliases", "duplicate alias in the same row")

    try:
        accessed = datetime.fromisoformat(row["accessed_on"]).replace(tzinfo=UTC)
    except ValueError:
        raise err("accessed_on", f"{row['accessed_on']!r} is not an ISO date (YYYY-MM-DD)") from None

    present = {
        "usable_kwh": True,
        "efficiency_kwh_per_km": True,
        "max_dc_kw": numbers["max_dc_kw"] is not None,
        "adapters": bool(adapters),
    }
    field_provenance: dict[str, Provenance] = {}
    for field, (url_col, type_col) in PROVENANCE_COLUMNS.items():
        url, stype = row[url_col], row[type_col]
        if not present[field]:
            if url or stype:
                raise err(url_col, f"source given but {field} is empty")
            continue
        field_provenance[field] = _provenance(url, stype, url_col, type_col, accessed, err)

    return Vehicle(
        vehicle_id=vid,
        make=row["make"],
        model=row["model"],
        model_year=year,
        variant=row["variant"] or None,
        usable_kwh=numbers["usable_kwh"],
        efficiency_kwh_per_km=numbers["efficiency_kwh_per_km"],
        native_connectors=native,
        adapters=adapters,
        max_dc_kw=numbers["max_dc_kw"],
        aliases=aliases,
        field_provenance=field_provenance,
    )


def _number(row: dict[str, str], name: str, err: Any) -> float | None:
    raw = row[name]
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        raise err(name, f"{raw!r} is not a number") from None
    lo, hi = NUMERIC_RANGES[name]
    if not lo <= value <= hi:
        raise err(name, f"{value} outside plausible range [{lo}, {hi}] (unit mistake?)")
    return value


def _connectors(raw: str, column: str, err: Any) -> list[Connector]:
    if not raw:
        return []
    out: list[Connector] = []
    for token in raw.split(";"):
        token = token.strip()
        try:
            conn = Connector(token)
        except ValueError:
            raise err(column, f"unknown connector {token!r}; allowed: {[c.value for c in Connector]}") from None
        if conn in out:
            raise err(column, f"connector {token!r} listed twice")
        out.append(conn)
    return out


def _provenance(url: str, stype: str, url_col: str, type_col: str, accessed: datetime, err: Any) -> Provenance:
    if not url.startswith("https://"):
        raise err(url_col, f"{url!r}: a https source URL is required")
    if stype not in CURATED_SOURCE_TYPES:
        raise err(type_col, f"unknown source type {stype!r}; allowed: {sorted(CURATED_SOURCE_TYPES)}")
    host = url.split("/")[2].lower()
    if stype == "FUELECONOMY" and not (host == "fueleconomy.gov" or host.endswith(".fueleconomy.gov")):
        raise err(url_col, f"FUELECONOMY source must be on fueleconomy.gov, got {host!r}")
    return Provenance(source=stype, source_url=url, retrieved_at=accessed, status=DataStatus.STATIC)
