"""Energy, charging-time, power and compatibility rules of the oracle, with hand-computed answers."""

import pytest
from _oracle_world import ENERGY, off_for_detour, port, vehicle

from clickless.contracts import ChargerLevel, Connector
from evaluation.oracle.energy_reference import (
    CHARGE_TIME_EXCLUDED_VEHICLES,
    access_km,
    charge_minutes,
    detour_minutes,
    is_charge_time_excluded,
    leg_soc_drop_pct,
    meets_min_power,
    override_has_no_overlap,
    peak_kw,
    port_compatible,
    port_power_kw,
    station_rejection,
    usable_ports,
)

CM = ENERGY["charging_model"]
T = vehicle()  # 50 kWh, 0.2 kWh/km, max DC 80, CCS1 native, NACS adapter


def test_energy_drop():
    assert leg_soc_drop_pct(100, T) == pytest.approx(40.0)
    assert leg_soc_drop_pct(100, T, penalty=0.15) == pytest.approx(46.0)
    with pytest.raises(ValueError):
        leg_soc_drop_pct(-1, T)


def _minutes(soc_from, soc_to, port_kw, level="dc_fast"):
    lvl = ChargerLevel(level)
    return charge_minutes(soc_from, soc_to, peak_kw(port_kw, lvl, T, CM), lvl, T, CM)


@pytest.mark.parametrize(
    "soc_from,soc_to,kw,level,expected",
    [
        (20, 80, 100, "dc_fast", 25.5),  # peak min(100, 80) = 80: 30 kWh / 80 kW = 22.5 min + 3
        (20, 90, 100, "dc_fast", 34.875),  # + 5 kWh at 0.4 x 80 = 32 kW = 9.375 min
        (85, 95, 100, "dc_fast", 12.375),  # all above the taper: 5 kWh / 32 kW = 9.375 min + 3
        (20, 80, 10, "l2", 203.0),  # 10 kW x 0.9 = 9 kW: 30 kWh = 200 min + 3, no taper
        (20, 90, 10, "l2", 236.333333),  # 35 kWh / 9 kW = 233.33 min + 3 (AC: no taper)
        (50, 50, 100, "dc_fast", 0.0),  # nothing added: no session
    ],
)
def test_charge_minutes(soc_from, soc_to, kw, level, expected):
    assert _minutes(soc_from, soc_to, kw, level) == pytest.approx(expected)


def test_peak_without_vehicle_max_dc_uses_port_power():
    v = vehicle(max_dc=None)
    assert peak_kw(150, ChargerLevel.DC_FAST, v, CM) == 150


@pytest.mark.parametrize("override,expected_kw", [(None, 50.0), ({"dc_fast": 25.0}, 25.0), ({"dc_fast": 100.0}, 100.0)])
def test_unknown_power_fallback_and_override(override, expected_kw):
    kw, estimated = port_power_kw(port(kw=None), CM, override)
    assert (kw, estimated) == (expected_kw, True)


@pytest.mark.parametrize("fallback,expected", [(50.0, 39.0), (25.0, 75.0), (100.0, 25.5)])
def test_unknown_power_charge_time(fallback, expected):
    kw, _ = port_power_kw(port(kw=None), CM, {"dc_fast": fallback})
    assert _minutes(20, 80, kw) == pytest.approx(expected)  # 100 kW fallback is capped by the car's 80 kW


def test_unknown_l2_fallback():
    assert port_power_kw(port("J1772", "l2", None), CM) == (6.6, True)


def test_non_positive_power_is_unusable():
    assert port_power_kw(port(kw=0.0), CM) == (None, False)
    assert port_power_kw(port(kw=-5.0), CM) == (None, False)
    assert usable_ports([port(kw=0.0)], T, None, None, CM) == []
    assert station_rejection([port(kw=0.0)], T, None, None) == "power"
    with pytest.raises(ValueError):
        charge_minutes(20, 30, 0.0, ChargerLevel.DC_FAST, T, CM)


def test_min_power_uses_reported_power_and_unknown_never_satisfies():
    assert meets_min_power(port(kw=150), 150)
    assert not meets_min_power(port(kw=149.9), 150)
    assert not meets_min_power(port(kw=None), 1)
    assert meets_min_power(port(kw=None), None)
    assert station_rejection([port(kw=None)], T, None, 50) == "power"


@pytest.mark.parametrize(
    "connector,level,override,ok",
    [
        ("CCS1", "dc_fast", None, True),  # native, DC
        ("CCS1", "l2", None, True),  # native at any level
        ("NACS", "dc_fast", None, True),  # documented adapter at DC fast
        ("NACS", "l2", None, False),  # adapter never at AC (ADR 0012)
        ("CHADEMO", "dc_fast", None, False),  # not supported at all
        ("J1772", "l2", None, False),
        ("NACS", "dc_fast", ["CCS1"], False),  # override narrows to native ("left my adapter at home")
        ("CCS1", "dc_fast", ["NACS"], False),  # override narrows to the adapter
        ("NACS", "dc_fast", ["NACS"], True),
        ("NACS", "l2", ["NACS"], False),
        ("CCS1", "dc_fast", ["CCS1", "CHADEMO"], True),  # unsupported part of an override is ignored
        ("CHADEMO", "dc_fast", ["CCS1", "CHADEMO"], False),  # ...and never widens
    ],
)
def test_compatibility_adr_0012(connector, level, override, ok):
    assert port_compatible(port(connector, level), T, override) is ok


def test_override_without_overlap():
    assert override_has_no_overlap(T, [Connector.CHADEMO])
    assert not override_has_no_overlap(T, [Connector.CHADEMO, Connector.CCS1])
    assert not override_has_no_overlap(T, None)
    assert not override_has_no_overlap(T, [])  # [] means no override


def test_native_port_wins_a_power_tie():
    ports = usable_ports([port("NACS", kw=150), port("CCS1", kw=150)], T, None, None, CM)
    assert [p.connector.value for p in ports] == ["CCS1", "NACS"]
    assert ports[0].native and not ports[1].native


def test_detour_model():
    assert detour_minutes(0.0, ENERGY["placeholder_detour"]) == pytest.approx(2.0)
    assert detour_minutes(off_for_detour(6.0), ENERGY["placeholder_detour"]) == pytest.approx(6.0)
    assert access_km(1.0, ENERGY["placeholder_detour"]) == pytest.approx(1.3)


def test_leaf_is_flagged_for_charge_time_exclusion():
    assert is_charge_time_excluded("nissan-leaf-sv-plus-2024")
    assert not is_charge_time_excluded("fixture-spark-lr-2025")


def test_leaf_id_exists_in_curated_vehicle_table():
    from pathlib import Path

    csv = (Path(__file__).resolve().parents[2] / "data/curated/vehicles.csv").read_text(encoding="utf-8")
    ids = {line.split(",", 1)[0] for line in csv.splitlines()[1:]}
    assert CHARGE_TIME_EXCLUDED_VEHICLES <= ids
