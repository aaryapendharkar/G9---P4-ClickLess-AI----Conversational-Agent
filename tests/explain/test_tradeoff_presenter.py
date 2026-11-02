"""Focused tests for driver-facing descriptions of alternative plans."""

from clickless.contracts import (
    Alternative,
    ChargingStop,
    Connector,
    Itinerary,
    PlanCost,
)
from clickless.explain.tradeoff_presenter import render_alternative


def _alternative(
    *,
    label: str = "Fastest",
    deltas: dict[str, float] | None = None,
    gains: list[str] | None = None,
    losses: list[str] | None = None,
) -> Alternative:
    """Build a small alternative containing only fields used by the presenter."""

    stop = ChargingStop(
        station_id="test-anthem",
        station_name="Anthem",
        connector=Connector.CCS1,
        port_power_kw=150.0,
        along_route_km=50.0,
        detour_min=2.0,
        arrive_soc_pct=20.0,
        depart_soc_pct=60.0,
        charge_min_est=15.0,
    )
    itinerary = Itinerary(
        itinerary_id="alternative-plan",
        request_id="tradeoff-presenter-test",
        vehicle_id="test-vehicle",
        stops=[stop],
        legs=[],
        total_drive_min=100.0,
        total_charge_min=15.0,
        total_detour_min=2.0,
        arrival_soc_pct=20.0,
    )
    cost = PlanCost(
        profile="balanced",
        drive_min=100.0,
        charge_min=15.0,
        detour_penalty_min=2.0,
        stop_overhead_min=5.0,
        amenity_credit_min=0.0,
        evidence_credit_min=0.0,
        total_min=122.0,
        amenity_satisfaction=0.0,
    )

    return Alternative(
        label=label,
        itinerary=itinerary,
        cost=cost,
        deltas=deltas or {},
        gains=gains or [],
        losses=losses or [],
    )


def test_faster_alternative_translates_negative_time_delta() -> None:
    """A negative time delta should be described as quicker, not as -N."""

    description = render_alternative(
        _alternative(deltas={"total_time_min": -9.6})
    )

    assert "Fastest: stop at Anthem instead" in description
    assert "about 10 min quicker" in description
    assert "-10" not in description


def test_charging_difference_is_explicitly_an_estimate() -> None:
    """Charging duration is modeled, so its wording must retain that caveat."""

    description = render_alternative(
        _alternative(
            label="Best amenities",
            deltas={
                "detour_min": 2.7,
                "charge_min": 5.2,
            },
            gains=["food"],
        )
    )

    assert "about 3 min more detour" in description
    assert "about 5 min more charging (estimate)" in description
    assert "adds a lunch spot" in description


def test_preference_keys_are_translated_for_drivers() -> None:
    """Internal preference keys should not leak into the explanation."""

    description = render_alternative(
        _alternative(losses=["food", "restroom_wheelchair"])
    )

    assert "loses a lunch spot" in description
    assert "accessible restroom" in description
    assert "restroom_wheelchair" not in description


def test_subminute_differences_are_not_presented() -> None:
    """Tiny model differences should not be shown as meaningful trade-offs."""

    description = render_alternative(
        _alternative(
            deltas={
                "total_time_min": -0.6,
                "detour_min": 0.4,
                "charge_min": -0.8,
            }
        )
    )

    assert "no material difference recorded" in description
    assert "0 min" not in description


def test_at_most_two_numerical_differences_are_presented() -> None:
    """Alternatives should stay scannable even when every delta is meaningful."""

    description = render_alternative(
        _alternative(
            deltas={
                "total_time_min": 8.0,
                "detour_min": 4.0,
                "charge_min": 6.0,
            }
        )
    )

    assert "about 8 min longer" in description
    assert "about 4 min more detour" in description
    assert "more charging" not in description
