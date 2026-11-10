"""Tests for the deterministic energy and charging-time models."""

import pytest

from clickless.contracts import ChargerLevel
from clickless.planning.charging_time import estimate_charge_min
from clickless.planning.energy import energy_kwh, leg_feasible, soc_after


def test_energy_model(store):
    vehicle = store.vehicle("fixture-spark-lr-2025")

    assert energy_kwh(100, vehicle) == pytest.approx(19.0)
    assert soc_after(50, 100, vehicle) == pytest.approx(50 - 100 * 19 / 74)
    assert leg_feasible(50, 100, vehicle, min_end_soc_pct=10)
    assert not leg_feasible(20, 100, vehicle, min_end_soc_pct=10)


def test_charge_time_monotonic(store, cfg_a):
    """More energy takes longer; usable power reduces time up to the car limit."""

    vehicle = store.vehicle("fixture-spark-lr-2025")
    charging_model = cfg_a["energy"]["charging_model"]

    def charge_time(
        power_kw: float,
        start_soc_pct: float,
        target_soc_pct: float,
    ) -> float:
        return estimate_charge_min(
            vehicle,
            power_kw,
            ChargerLevel.DC_FAST,
            start_soc_pct,
            target_soc_pct,
            charging_model,
        )

    assert (
        charge_time(150, 20, 60) < charge_time(150, 20, 80) < charge_time(150, 20, 95)
    )
    assert charge_time(50, 20, 60) > charge_time(150, 20, 60)
    assert charge_time(350, 20, 60) == charge_time(230, 20, 60)
    assert charge_time(150, 60, 60) == 0.0


@pytest.mark.parametrize(
    "charger_level",
    [
        ChargerLevel.DC_FAST,
        ChargerLevel.L2,
        ChargerLevel.L1,
    ],
)
def test_unknown_power_uses_configured_estimate(store, cfg_a, charger_level):
    """Unknown source power uses config without becoming a station fact."""

    vehicle = store.vehicle("fixture-spark-lr-2025")
    charging_model = cfg_a["energy"]["charging_model"]
    configured_power_kw = charging_model["unknown_power_kw"][charger_level.value]

    estimated_with_unknown_power = estimate_charge_min(
        vehicle,
        port_power_kw=None,
        level=charger_level,
        soc_from_pct=20,
        soc_to_pct=40,
        model_cfg=charging_model,
    )
    estimated_with_explicit_power = estimate_charge_min(
        vehicle,
        port_power_kw=configured_power_kw,
        level=charger_level,
        soc_from_pct=20,
        soc_to_pct=40,
        model_cfg=charging_model,
    )

    assert estimated_with_unknown_power == pytest.approx(estimated_with_explicit_power)
