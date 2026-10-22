"""Charging-duration ESTIMATE (TA feedback). Owner: Hriday.

v0 model (documented in docs/TRADEOFFS.md):
  peak  = min(port power, vehicle max DC power)        for DC fast ports
        = port power * ac_efficiency                    for L2 ports
  below taper_start SoC: charge at peak; above it: at taper_power_fraction * peak (DC only)
  minutes = 60 * (kWh_below / peak + kWh_above / tapered_peak) + session_overhead_min
It is shown to drivers as "about N min (estimate)" and its uncertainty is handled by the
sensitivity analysis (charge time x0.75 / x1.25) rather than by pretending to be precise.
TODO(Hriday): unknown port power policy (currently conservative defaults), vehicle AC limits,
unit tests for monotonicity (more SoC => more time, more power => less time).
"""

from __future__ import annotations

from typing import Any

from ..contracts import ChargerLevel, Vehicle

_UNKNOWN_POWER_KW = {ChargerLevel.DC_FAST: 50.0, ChargerLevel.L2: 6.6, ChargerLevel.L1: 1.4}


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
    power = port_power_kw if port_power_kw else _UNKNOWN_POWER_KW[level]
    if level == ChargerLevel.DC_FAST:
        peak = min(power, vehicle.max_dc_kw or power)
        tapered = peak * model_cfg["taper_power_fraction"]
    else:
        peak = power * model_cfg["ac_efficiency"]
        tapered = peak  # AC charging is roughly flat
    taper_start = model_cfg["taper_start_soc_pct"]
    kwh_below = max(0.0, min(soc_to_pct, taper_start) - soc_from_pct) / 100 * vehicle.usable_kwh
    kwh_above = max(0.0, soc_to_pct - max(soc_from_pct, taper_start)) / 100 * vehicle.usable_kwh
    return 60.0 * (kwh_below / peak + kwh_above / tapered) + model_cfg["session_overhead_min"]
