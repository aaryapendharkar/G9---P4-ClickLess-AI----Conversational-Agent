"""ADR 0010 / issue #34: unknown station power stays unknown end to end. Owner: Aarya.

The configured fallback (`charging_model.unknown_power_kw` in configs/energy.yaml) only feeds the
charging-duration *estimate*. It must never be written back as station data, shown as a sourced
power value, or satisfy an explicit minimum-power request. These are the properties checked in
the PR #35 review, locked in here.
"""

import pytest

from clickless.contracts import ChargerLevel
from clickless.data import DataStore
from clickless.data.validate import blocking_problems, validate_store
from clickless.pipeline import build_pipeline


@pytest.fixture
def unknown_power_store(store):
    """The fixture world with every DC fast port's power removed (as for older AFDC records)."""
    stations = [
        s.model_copy(
            update={
                "ports": [
                    p.model_copy(update={"power_kw": None}) if p.level == ChargerLevel.DC_FAST else p
                    for p in s.ports
                ]
            },
            deep=True,
        )
        for s in store.stations.values()
    ]
    return DataStore(stations, list(store.vehicles.values()), store.amenities, store.places, store.routes_dir)


def _dc_powers(store):
    return [p.power_kw for s in store.stations.values() for p in s.ports if p.level == ChargerLevel.DC_FAST]


def test_fixture_world_has_dc_ports_to_blank(store):
    assert _dc_powers(store) and all(p is not None for p in _dc_powers(store))


def test_planning_never_writes_the_fallback_back(cfg_a, unknown_power_store, demo_text):
    result = build_pipeline(cfg_a, unknown_power_store).run(demo_text, "unknown-power-1")

    assert result.status == "ok"
    assert all(p is None for p in _dc_powers(unknown_power_store))  # station facts unchanged
    stops = result.alternatives[0].itinerary.stops
    assert stops and all(stop.port_power_kw is None for stop in stops)  # the plan keeps "unknown"
    assert all(stop.charge_min_est > 0 for stop in stops)  # but still gets a duration estimate


def test_explanation_says_unknown_power_and_estimate(cfg_a, unknown_power_store, demo_text):
    fallback_kw = cfg_a["energy"]["charging_model"]["unknown_power_kw"]["dc_fast"]
    explanation = build_pipeline(cfg_a, unknown_power_store).run(demo_text, "unknown-power-2").explanation

    assert "unknown power" in explanation
    assert "(estimate)" in explanation
    assert f"{fallback_kw:g} kW" not in explanation  # the fallback is never presented as a sourced value


def test_unknown_power_never_satisfies_min_power(cfg_a, unknown_power_store, demo_text):
    result = build_pipeline(cfg_a, unknown_power_store).run(demo_text + " using fast chargers", "unknown-power-3")

    assert result.request.min_power_kw is not None
    assert result.status == "infeasible"
    assert result.alternatives == []


def test_validator_warns_but_does_not_block(unknown_power_store):
    problems = validate_store(unknown_power_store)

    assert len(problems["dc_power_unknown"]) == len(_dc_powers(unknown_power_store))
    assert blocking_problems(problems) == {}
