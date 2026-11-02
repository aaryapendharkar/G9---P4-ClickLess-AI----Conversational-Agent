"""Unit tests for departure-charge target selection."""

import pytest

from clickless.contracts import TripRequest
from clickless.planning.charging_policy import departure_soc_target


def _request(profile: str) -> TripRequest:
    """Build the smallest request needed by the charging policy."""

    return TripRequest(
        request_id=f"charging-policy-{profile}",
        raw_text="charging policy test",
        tradeoff_profile=profile,
    )


@pytest.mark.parametrize("profile", ["balanced", "fastest"])
def test_non_comfort_profiles_keep_just_enough_target(cfg_a, profile):
    """Ordinary and time-focused planning must not add a comfort top-up."""

    target_soc_pct = departure_soc_target(
        _request(profile),
        {"food"},
        just_enough_soc_pct=43.0,
        energy_config=cfg_a["energy"],
    )

    assert target_soc_pct == 43.0


def test_comfort_food_stop_uses_taper_start_as_efficient_target(cfg_a):
    """Meal-stop charging reaches the existing pre-taper threshold."""

    target_soc_pct = departure_soc_target(
        _request("comfort"),
        {"food"},
        just_enough_soc_pct=43.0,
        energy_config=cfg_a["energy"],
    )

    assert target_soc_pct == pytest.approx(
        cfg_a["energy"]["charging_model"]["taper_start_soc_pct"]
    )


def test_comfort_policy_never_lowers_higher_required_charge(cfg_a):
    """A leg requiring more than 80% retains its safety-driven target."""

    target_soc_pct = departure_soc_target(
        _request("comfort"),
        {"food"},
        just_enough_soc_pct=90.0,
        energy_config=cfg_a["energy"],
    )

    assert target_soc_pct == 90.0


def test_comfort_policy_respects_maximum_departure_charge(cfg_a):
    """The top-up cannot exceed the energy model's configured ceiling."""

    constrained_energy_config = {
        **cfg_a["energy"],
        "charge_target_max_soc_pct": 70.0,
    }

    target_soc_pct = departure_soc_target(
        _request("comfort"),
        {"food"},
        just_enough_soc_pct=60.0,
        energy_config=constrained_energy_config,
    )

    assert target_soc_pct == 70.0


def test_comfort_without_satisfied_food_keeps_just_enough_target(cfg_a):
    """Comfort does not invent a meal stop when only another amenity matches."""

    target_soc_pct = departure_soc_target(
        _request("comfort"),
        {"restroom"},
        just_enough_soc_pct=43.0,
        energy_config=cfg_a["energy"],
    )

    assert target_soc_pct == 43.0
