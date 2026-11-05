"""Charging-duration ESTIMATE (TA feedback). Owner: Hriday.

v0 model (documented in docs/TRADEOFFS.md):
  peak  = min(port power, vehicle max DC power)        for DC fast ports
        = port power * ac_efficiency                    for AC ports
  below taper_start SoC: charge at peak; above it: at taper_power_fraction * peak (DC only)
  minutes = 60 * (kWh_below / peak + kWh_above / tapered_peak) + session_overhead_min

When a source does not report station power, the estimate uses the configured fallback for that
charger level. The station fact remains unknown, and an explicit minimum-power constraint still
rejects it. Every displayed duration is labelled as an estimate.

Charge-time uncertainty is evaluated by scaling estimates in sensitivity analysis rather than
presenting the simple model as a vehicle-specific charging curve.

TODO(Hriday+Aarya): add vehicle AC acceptance limits if the shared Vehicle contract gains a
sourced maximum-AC-power field.
"""

from __future__ import annotations

from typing import Any

from ..contracts import ChargerLevel, Vehicle


def _estimate_power_kw(
    port_power_kw: float | None,
    level: ChargerLevel,
    model_cfg: dict[str, Any],
) -> float:
    """Return reported station power or the configured estimate for unknown power.

    The fallback supports a duration estimate only. It does not modify the
    station record or prove that a minimum-power constraint is satisfied.
    """

    if port_power_kw is not None:
        return port_power_kw

    unknown_power_by_level = model_cfg["unknown_power_kw"]
    return float(unknown_power_by_level[level.value])


def estimate_charge_min(
    vehicle: Vehicle,
    port_power_kw: float | None,
    level: ChargerLevel,
    soc_from_pct: float,
    soc_to_pct: float,
    model_cfg: dict[str, Any],
) -> float:
    if soc_to_pct <= soc_from_pct:
        return 0.0
    power = _estimate_power_kw(
        port_power_kw,
        level,
        model_cfg,
    )
    if level == ChargerLevel.DC_FAST:
        peak = min(power, vehicle.max_dc_kw or power)
        tapered = peak * model_cfg["taper_power_fraction"]
    else:
        peak = power * model_cfg["ac_efficiency"]
        tapered = peak  # AC charging is roughly flat
    taper_start = model_cfg["taper_start_soc_pct"]
    kwh_below = (
        max(0.0, min(soc_to_pct, taper_start) - soc_from_pct) / 100 * vehicle.usable_kwh
    )
    kwh_above = (
        max(0.0, soc_to_pct - max(soc_from_pct, taper_start)) / 100 * vehicle.usable_kwh
    )
    return (
        60.0 * (kwh_below / peak + kwh_above / tapered)
        + model_cfg["session_overhead_min"]
    )
