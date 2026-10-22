"""Documented energy model (proposal §III-D, eq. 3). Owner: Hriday.

E_req = d * e (kWh), optionally inflated by an efficiency penalty for the robust-feasibility test.
Feasible leg: E_avail - E_req >= E_reserve. We work in SoC percent of usable capacity.
The oracle re-implements this independently from configs/energy.yaml -- keep both in sync with
the written definition, not with each other's code.
"""

from __future__ import annotations

from ..contracts import Vehicle


def energy_kwh(distance_km: float, vehicle: Vehicle, penalty: float = 0.0) -> float:
    return distance_km * vehicle.efficiency_kwh_per_km * (1.0 + penalty)


def kwh_to_soc_pct(kwh: float, vehicle: Vehicle) -> float:
    return 100.0 * kwh / vehicle.usable_kwh


def soc_after(soc_start_pct: float, distance_km: float, vehicle: Vehicle, penalty: float = 0.0) -> float:
    return soc_start_pct - kwh_to_soc_pct(energy_kwh(distance_km, vehicle, penalty), vehicle)


def leg_feasible(soc_start_pct: float, distance_km: float, vehicle: Vehicle, min_end_soc_pct: float,
                 penalty: float = 0.0) -> bool:
    return soc_after(soc_start_pct, distance_km, vehicle, penalty) >= min_end_soc_pct
