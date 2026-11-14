"""AFDC (NREL Alternative Fuel Stations API) ingestion. Owner: Aarya.

  1. fetch_stations(): one request for all public, non-planned EV stations -> data/raw/afdc/*.json
     (the API key is read from the caller and never written to disk or to error messages);
  2. to_station(): one raw record -> contracts.Station with Provenance(source="AFDC",
     source_record_id=<id>, source_updated_at=<updated_at>, status=CACHED);
  3. stations_from_raw(): every record of a saved download, plus a report of skipped records.

Port mapping rules (ADR 0010):
  * Records with `ev_charging_units` use them: one ChargingPort per (connector, level, power),
    power from the unit, counts summed. CCS1/CHAdeMO are always DC fast and J1772 always AC;
    NACS takes the unit's level. A connector listed only in `ev_connector_types` is still kept
    (from the station counts, power unknown).
  * Older records without units fall back to `ev_connector_types` + the station's port counts.
    Power is then unknown (power_kw=None, the contract's "not reported" flag; the planner uses a
    conservative default and the explanation says "unknown power").
  * Records with no supported connector (only household outlets or truck MCS) or without
    coordinates are skipped and counted, never silently.
  * Anything the docs do not list (new connector code, status code or charging level) raises
    AfdcSchemaError so the mapping is updated on purpose.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import repo_path
from ..contracts import ChargerLevel, ChargingPort, Connector, DataStatus, Provenance, Station, StationStatus
from .connectors import normalize_afdc_connector

# NREL retired developer.nrel.gov on 2026-05-29; the same API and keys are served from developer.nlr.gov.
API_URL = "https://developer.nlr.gov/api/alt-fuel-stations/v1.json"
RECORD_URL = "https://developer.nlr.gov/api/alt-fuel-stations/v1/{id}.json"  # key not included
FETCH_PARAMS: dict[str, str] = {
    "fuel_type": "ELEC",
    "access": "public",
    "status": "E,T",  # available + temporarily unavailable; planned stations are not usable
    "country": "US",
    "limit": "all",
}

STATUS_MAP = {
    "E": StationStatus.AVAILABLE,
    "T": StationStatus.TEMPORARILY_UNAVAILABLE,
    "P": StationStatus.PLANNED,
}
ACCESS_MAP = {"public": "public", "private": "restricted"}
# Documented: "1", "2", "dc_fast". Spelling variants are normalized by _level() first.
LEVEL_MAP = {"dcfast": ChargerLevel.DC_FAST, "2": ChargerLevel.L2, "1": ChargerLevel.L1}
_DC_ONLY = {Connector.CCS1, Connector.CHADEMO}


class AfdcSchemaError(ValueError):
    """The response contains a value the AFDC docs (and our mapping) do not cover."""


class AfdcRecordSkipped(ValueError):
    """A record that cannot become a usable Station; counted in the build report."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


@dataclass
class ConversionReport:
    total: int = 0
    converted: int = 0
    skipped: Counter = field(default_factory=Counter)
    examples: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    unknown_power_dc_ports: int = 0

    def skip(self, reason: str, record_id: Any) -> None:
        self.skipped[reason] += 1
        if len(self.examples[reason]) < 5:
            self.examples[reason].append(str(record_id))

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_records": self.total,
            "converted": self.converted,
            "skipped": dict(self.skipped),
            "skipped_examples": {k: v for k, v in self.examples.items()},
            "dc_ports_with_unknown_power": self.unknown_power_dc_ports,
        }


# ---------------------------------------------------------------- download


def fetch_stations(
    api_key: str,
    state: str | None = None,
    out_dir: str = "data/raw/afdc",
    *,
    base_url: str = API_URL,
    timeout_s: float = 300,
) -> str:
    """Download all matching station records and return the path written.

    One request (`limit=all` is supported by the API). The saved file has `_meta` (retrieval time,
    URL and parameters WITHOUT the key) and the untouched API `response`.
    """
    import requests  # imported here so the offline pipeline does not need it

    if not api_key:
        raise ValueError("NREL API key missing: set NREL_API_KEY (see .env.example)")
    params = dict(FETCH_PARAMS)
    if state:
        params["state"] = state.upper()
    retrieved_at = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        resp = requests.get(base_url, params={**params, "api_key": api_key}, timeout=timeout_s)
    except requests.RequestException as e:  # message may contain the URL with the key
        raise RuntimeError(f"AFDC request failed: {type(e).__name__}") from None
    if resp.status_code != 200:
        raise RuntimeError(f"AFDC request failed: HTTP {resp.status_code}: {resp.text[:300].replace(api_key, '***')}")
    try:
        payload = resp.json()
    except ValueError:
        raise RuntimeError("AFDC response was not JSON; try again later") from None
    if "fuel_stations" not in payload:
        raise AfdcSchemaError(f"AFDC response has no 'fuel_stations' (keys: {sorted(payload)[:10]})")
    out = repo_path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"afdc_{(state or 'US').upper()}_{retrieved_at:%Y%m%dT%H%M%SZ}.json"
    meta = {
        "retrieved_at": retrieved_at.isoformat(),
        "url": base_url,
        "params": params,
        "total_results": payload.get("total_results"),
    }
    path.write_text(json.dumps({"_meta": meta, "response": payload}), encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------- conversion


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip().replace(" UTC", "Z")
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _connector(code: str) -> Connector | None:
    try:
        return normalize_afdc_connector(code)
    except KeyError:
        raise AfdcSchemaError(f"unknown AFDC connector code {code!r}; add it to data/connectors.py") from None


def _power(value: Any) -> float | None:
    try:
        p = float(value)
    except (TypeError, ValueError):
        return None
    return p if p > 0 else None


def _count(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return 1  # the port exists (the unit lists it) but its count is not reported
    return max(n, 1)


def _level(value: Any) -> ChargerLevel:
    """'dc_fast', 'DC Fast', 'level 2', 'L2', 2 ... -> ChargerLevel; anything else is a schema error."""
    key = str(value if value is not None else "").strip().lower()
    for noise in ("level", "_", "-", " "):
        key = key.replace(noise, "")
    key = {"l1": "1", "l2": "2"}.get(key, key)
    if key not in LEVEL_MAP:
        raise AfdcSchemaError(f"unknown ev_charging_units charging_level {value!r}")
    return LEVEL_MAP[key]


def _int(value: Any) -> int:
    """Port counts: None, '', non-numeric -> 0 (= not reported)."""
    try:
        return max(int(float(value)), 0)
    except (TypeError, ValueError):
        return 0


def _ports_from_units(units: list[dict[str, Any]]) -> list[ChargingPort]:
    merged: dict[tuple[Connector, ChargerLevel, float | None], int] = defaultdict(int)
    for unit in units:
        unit_level = _level(unit.get("charging_level"))
        for code, spec in (unit.get("connectors") or {}).items():
            conn = _connector(code)
            if conn is None:
                continue
            spec = spec or {}
            if conn in _DC_ONLY:
                level = ChargerLevel.DC_FAST
            elif conn == Connector.J1772:
                level = ChargerLevel.L1 if unit_level == ChargerLevel.L1 else ChargerLevel.L2
            else:
                level = unit_level
            merged[(conn, level, _power(spec.get("power_kw")))] += _count(spec.get("port_count"))
    return [
        ChargingPort(connector=c, level=lvl, power_kw=p, count=n)
        for (c, lvl, p), n in sorted(merged.items(), key=lambda kv: (kv[0][0].value, kv[0][1].value, kv[0][2] or 0))
    ]


def _ports_from_counts(record: dict[str, Any]) -> list[ChargingPort]:
    conns = [c for c in (_connector(code) for code in record.get("ev_connector_types") or []) if c is not None]
    conns = list(dict.fromkeys(conns))
    dc_num = _int(record.get("ev_dc_fast_num"))
    l2_num = _int(record.get("ev_level2_evse_num"))
    l1_num = _int(record.get("ev_level1_evse_num"))
    network = str(record.get("ev_network") or "")
    dc_types = [c for c in conns if c in _DC_ONLY]
    nacs_dc = Connector.NACS in conns and dc_num > 0 and (network == "Tesla" or l2_num == 0)
    n_dc = len(dc_types) + (1 if nacs_dc else 0)
    per_dc = max(1, dc_num // n_dc) if n_dc else 1
    ports = []
    for c in conns:
        if c in _DC_ONLY or (c == Connector.NACS and nacs_dc):
            ports.append(ChargingPort(connector=c, level=ChargerLevel.DC_FAST, power_kw=None, count=per_dc))
        else:  # J1772, or NACS at an AC (destination) location
            level = ChargerLevel.L1 if (l2_num == 0 and l1_num > 0) else ChargerLevel.L2
            ports.append(ChargingPort(connector=c, level=level, power_kw=None, count=max(1, l2_num or l1_num)))
    return ports


def to_station(record: dict[str, Any], retrieved_at: str | datetime) -> Station:
    """Normalize one raw AFDC record. Raises AfdcRecordSkipped or AfdcSchemaError."""
    rid = record.get("id")
    if rid is None:
        raise AfdcRecordSkipped("missing_id")
    try:
        lat, lon = float(record["latitude"]), float(record["longitude"])
    except (KeyError, TypeError, ValueError):
        raise AfdcRecordSkipped("missing_coordinates", str(rid)) from None

    status_code = record.get("status_code")
    if status_code not in STATUS_MAP:
        raise AfdcSchemaError(f"station {rid}: unknown status_code {status_code!r}")
    access_code = str(record.get("access_code") or "").lower()
    if access_code not in ACCESS_MAP:
        raise AfdcSchemaError(f"station {rid}: unknown access_code {record.get('access_code')!r}")

    units = record.get("ev_charging_units") or []
    ports = _ports_from_counts(record)
    if units:  # unit data wins; connectors listed only at station level are kept (power unknown)
        unit_ports = _ports_from_units(units)
        covered = {p.connector for p in unit_ports}
        ports = unit_ports + [p for p in ports if p.connector not in covered]
    if not ports:
        raise AfdcRecordSkipped("no_supported_connector", str(rid))

    if isinstance(retrieved_at, str):
        retrieved = _parse_dt(retrieved_at)
        if retrieved is None:
            raise ValueError(f"bad retrieved_at {retrieved_at!r}")
    else:
        retrieved = retrieved_at
    prov = Provenance(
        source="AFDC",
        source_record_id=str(rid),
        source_url=RECORD_URL.format(id=rid),
        retrieved_at=retrieved,
        source_updated_at=_parse_dt(record.get("updated_at")),
        status=DataStatus.CACHED,
    )
    return Station(
        station_id=f"afdc:{rid}",
        name=(record.get("station_name") or "").strip() or f"AFDC station {rid}",
        lat=lat,
        lon=lon,
        network=record.get("ev_network") or None,
        access=ACCESS_MAP[access_code],
        status=STATUS_MAP[status_code],
        ports=ports,
        provenance=prov,
    )


def stations_from_records(
    records: list[dict[str, Any]], retrieved_at: str | datetime
) -> tuple[list[Station], ConversionReport]:
    """Convert every record; skipped records are counted by reason, schema surprises raise."""
    report = ConversionReport(total=len(records))
    stations: list[Station] = []
    seen: set[str] = set()
    for rec in records:
        try:
            st = to_station(rec, retrieved_at)
        except AfdcRecordSkipped as e:
            report.skip(e.reason, rec.get("id"))
            continue
        if st.station_id in seen:
            report.skip("duplicate_id", rec.get("id"))
            continue
        seen.add(st.station_id)
        report.unknown_power_dc_ports += sum(
            1 for p in st.ports if p.level == ChargerLevel.DC_FAST and p.power_kw is None
        )
        stations.append(st)
    report.converted = len(stations)
    return stations, report


def stations_from_raw(path: str | Path) -> tuple[list[Station], ConversionReport, dict[str, Any]]:
    """Load a file written by fetch_stations() and convert it."""
    saved = json.loads(Path(path).read_text(encoding="utf-8"))
    meta = saved["_meta"]
    stations, report = stations_from_records(saved["response"]["fuel_stations"], meta["retrieved_at"])
    return stations, report, meta
