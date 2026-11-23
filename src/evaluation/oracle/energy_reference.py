"""Reference implementation of the documented energy model, written from configs/energy.yaml and
the proposal text -- NOT copied from clickless.planning.energy. Owner: Ian.

If this and the planner disagree on a case, that is a finding: fix the documentation first,
then whichever implementation is wrong. Never change this file to make it agree with the planner.

Sources (see docs/EVALUATION_ORACLE.md for every gap and the decision taken):
  * energy: E_req = distance x vehicle efficiency (proposal, ADR 0009: EPA wall-to-wheels)
  * charging time: docs/TRADEOFFS.md section 3, configs/energy.yaml `charging_model`
  * unknown power: ADR 0010
  * connector compatibility: ADR 0012
  * LEAF charge-time exclusion: ADR 0009
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from clickless.contracts import ChargerLevel, ChargingPort, Connector, Vehicle

# ADR 0009: the LEAF stays in feasibility metrics but is excluded from charge-time accuracy and
# charge-time sensitivity results. The vehicle table has no flag for this, so it is listed by id.
CHARGE_TIME_EXCLUDED_VEHICLES: frozenset[str] = frozenset({"nissan-leaf-sv-plus-2024"})


def is_charge_time_excluded(vehicle_id: str | None) -> bool:
    return vehicle_id in CHARGE_TIME_EXCLUDED_VEHICLES


# --------------------------------------------------------------------------- energy


def leg_energy_kwh(distance_km: float, vehicle: Vehicle, penalty: float = 0.0) -> float:
    """E_req = d x e, optionally with a consumption penalty (0.15 = +15 %)."""
    if distance_km < 0:
        raise ValueError(f"negative distance {distance_km}")
    return distance_km * vehicle.efficiency_kwh_per_km * (1.0 + penalty)


def leg_soc_drop_pct(distance_km: float, vehicle: Vehicle, penalty: float = 0.0) -> float:
    """E_req as a percentage of USABLE capacity (ADR 0009: usable_kwh is net capacity)."""
    return leg_energy_kwh(distance_km, vehicle, penalty) / vehicle.usable_kwh * 100.0


# --------------------------------------------------------------------------- compatibility (ADR 0012)


def allowed_connectors(vehicle: Vehicle, override: Iterable[Connector] | None) -> tuple[set[Connector], set[Connector]]:
    """(native, adapter-only) connectors after applying the override. The override only narrows."""
    native = set(vehicle.native_connectors)
    adapters = set(vehicle.adapters) - native
    wanted = set(override or [])
    if wanted:  # None and [] both mean "no override"
        native &= wanted
        adapters &= wanted
    return native, adapters


def override_has_no_overlap(vehicle: Vehicle, override: Iterable[Connector] | None) -> bool:
    """A non-empty override that shares no connector with the vehicle's supported set.

    Decision (docs/EVALUATION_ORACLE.md, gap 1): the request is infeasible; the override is NOT
    dropped back to "no override", so a parser mistake can never widen the compatible set.
    """
    wanted = set(override or [])
    return bool(wanted) and not (wanted & vehicle.supported_connectors())


def port_compatible(port: ChargingPort, vehicle: Vehicle, override: Iterable[Connector] | None = None) -> bool:
    """Native connector at any level, or a documented adapter at a DC fast port only."""
    native, adapters = allowed_connectors(vehicle, override)
    if port.connector in native:
        return True
    return port.connector in adapters and port.level == ChargerLevel.DC_FAST


def is_native(port: ChargingPort, vehicle: Vehicle) -> bool:
    return port.connector in set(vehicle.native_connectors)


# --------------------------------------------------------------------------- power


def port_power_kw(
    port: ChargingPort, charging_model: Mapping[str, Any], unknown_override: Mapping[str, float] | None = None
) -> tuple[float | None, bool]:
    """(power used for the duration ESTIMATE, estimated?). (None, False) = unusable (power <= 0).

    Unknown power (None) uses `charging_model.unknown_power_kw[level]` (ADR 0010), which a
    sensitivity run can override per level. The fallback never becomes a station fact.
    """
    if port.power_kw is None:
        table = {**charging_model["unknown_power_kw"], **(unknown_override or {})}
        return float(table[port.level.value]), True
    if port.power_kw <= 0:
        return None, False
    return float(port.power_kw), False


def meets_min_power(port: ChargingPort, min_power_kw: float | None) -> bool:
    """Compared with the station's reported port power. Unknown power never satisfies it."""
    if min_power_kw is None:
        return True
    return port.power_kw is not None and port.power_kw >= min_power_kw


def peak_kw(port_kw: float, level: ChargerLevel, vehicle: Vehicle, charging_model: Mapping[str, Any]) -> float:
    """min(port power, vehicle max DC); AC (L2/L1) sessions x ac_efficiency (TRADEOFFS section 3)."""
    peak = port_kw if vehicle.max_dc_kw is None else min(port_kw, vehicle.max_dc_kw)
    if level != ChargerLevel.DC_FAST:
        peak *= float(charging_model["ac_efficiency"])
    return peak


def charge_minutes(
    soc_from: float,
    soc_to: float,
    peak: float,
    level: ChargerLevel,
    vehicle: Vehicle,
    charging_model: Mapping[str, Any],
) -> float:
    """Charging-duration ESTIMATE, including the session overhead. 0 when no energy is added.

    Constant `peak` up to taper_start_soc_pct, then taper_power_fraction x peak above it. The
    taper applies to DC sessions only (TRADEOFFS section 3 says "(DC)"; gap 6).
    """
    if soc_to <= soc_from:
        return 0.0
    if peak <= 0:
        raise ValueError(f"non-positive charging power {peak}")
    taper = float(charging_model["taper_start_soc_pct"])
    frac = float(charging_model["taper_power_fraction"])
    if level == ChargerLevel.DC_FAST:
        below = max(0.0, min(soc_to, taper) - soc_from)
        above = max(0.0, soc_to - max(soc_from, taper))
    else:
        below, above = soc_to - soc_from, 0.0
    kwh_below = below / 100.0 * vehicle.usable_kwh
    kwh_above = above / 100.0 * vehicle.usable_kwh
    hours = kwh_below / peak + kwh_above / (peak * frac)
    return hours * 60.0 + float(charging_model["session_overhead_min"])


@dataclass(frozen=True)
class PortChoice:
    connector: Connector
    level: ChargerLevel
    power_kw: float | None  # the station's reported value (None = unknown), never the fallback
    peak_kw: float  # power used for the estimate (fallback-based when estimated)
    estimated: bool  # True when power_kw is unknown and the fallback was used
    native: bool


def usable_ports(
    ports: Iterable[ChargingPort],
    vehicle: Vehicle,
    override: Iterable[Connector] | None,
    min_power_kw: float | None,
    charging_model: Mapping[str, Any],
    unknown_override: Mapping[str, float] | None = None,
) -> list[PortChoice]:
    """Compatible ports that satisfy min_power_kw and have positive power, fastest first.

    Ties go to native connectors (ADR 0012: native before adapter).
    """
    out: list[PortChoice] = []
    for p in ports:
        if not port_compatible(p, vehicle, override) or not meets_min_power(p, min_power_kw):
            continue
        kw, estimated = port_power_kw(p, charging_model, unknown_override)
        if kw is None:
            continue
        out.append(
            PortChoice(
                p.connector,
                p.level,
                p.power_kw,
                peak_kw(kw, p.level, vehicle, charging_model),
                estimated,
                is_native(p, vehicle),
            )
        )
    out.sort(key=lambda c: (-c.peak_kw, not c.native))
    return out


def station_rejection(
    ports: Iterable[ChargingPort],
    vehicle: Vehicle,
    override: Iterable[Connector] | None,
    min_power_kw: float | None,
) -> str | None:
    """Why a station has no usable port: 'connector' (nothing compatible) or 'power'; None if usable."""
    ports = list(ports)
    compatible = [p for p in ports if port_compatible(p, vehicle, override)]
    if not compatible:
        return "connector"
    if not any(meets_min_power(p, min_power_kw) and (p.power_kw is None or p.power_kw > 0) for p in compatible):
        return "power"
    return None


# --------------------------------------------------------------------------- detour (energy.yaml placeholder_detour)


def access_km(offroute_km: float, detour_cfg: Mapping[str, Any]) -> float:
    """One-way road km between the route and the station (straight line x road_factor)."""
    return offroute_km * float(detour_cfg["road_factor"])


def detour_minutes(offroute_km: float, detour_cfg: Mapping[str, Any]) -> float:
    """fixed_min + round trip (route -> station -> route) at offroute_speed_kmh (gap 3)."""
    return (
        float(detour_cfg["fixed_min"])
        + 2.0 * access_km(offroute_km, detour_cfg) / float(detour_cfg["offroute_speed_kmh"]) * 60.0
    )
