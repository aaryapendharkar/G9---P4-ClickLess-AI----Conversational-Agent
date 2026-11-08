"""Generated boundary tests for complete-itinerary verification."""

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

_PROPERTY_SETTINGS = settings(
    max_examples=30,
    derandomize=True,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


@pytest.fixture
def verified_plan(pipe_a, demo_text):
    """Return one valid recommendation used as an immutable test baseline."""

    result = pipe_a.run(
        demo_text,
        request_id="verifier-property-baseline",
    )
    assert result.status == "ok"
    assert result.verification is not None
    assert result.verification.ok
    return result


def _verification_codes(pipe_a, itinerary, request) -> set[str]:
    """Verify a copied plan and return only its machine-readable codes."""

    vehicle = pipe_a.store.vehicle(itinerary.vehicle_id)
    route = pipe_a.router.route(
        request.origin,
        request.destination,
    )
    report = pipe_a.verifier.verify(
        itinerary,
        request,
        vehicle,
        route,
    )
    return {violation.code for violation in report.violations}


@_PROPERTY_SETTINGS
@given(
    distance_change_km=st.floats(
        min_value=-0.099,
        max_value=0.099,
        allow_nan=False,
        allow_infinity=False,
    )
)
def test_leg_distance_change_within_tolerance_is_accepted(
    pipe_a,
    verified_plan,
    distance_change_km: float,
) -> None:
    """Human-scale rounding inside 0.1 km must not become a violation."""

    itinerary = verified_plan.alternatives[0].itinerary
    changed_leg = itinerary.legs[0].model_copy(
        update={
            "distance_km": itinerary.legs[0].distance_km
            + distance_change_km,
        }
    )
    changed_itinerary = itinerary.model_copy(
        update={
            "legs": [
                changed_leg,
                *itinerary.legs[1:],
            ]
        }
    )

    codes = _verification_codes(
        pipe_a,
        changed_itinerary,
        verified_plan.request,
    )

    assert "route_distance" not in codes


@_PROPERTY_SETTINGS
@given(
    distance_change_magnitude_km=st.floats(
        min_value=0.101,
        max_value=20.0,
        allow_nan=False,
        allow_infinity=False,
    ),
    direction=st.sampled_from([-1.0, 1.0]),
)
def test_leg_distance_change_beyond_tolerance_is_rejected(
    pipe_a,
    verified_plan,
    distance_change_magnitude_km: float,
    direction: float,
) -> None:
    """Any generated change beyond 0.1 km must be detected."""

    itinerary = verified_plan.alternatives[0].itinerary
    changed_leg = itinerary.legs[0].model_copy(
        update={
            "distance_km": itinerary.legs[0].distance_km
            + direction * distance_change_magnitude_km,
        }
    )
    changed_itinerary = itinerary.model_copy(
        update={
            "legs": [
                changed_leg,
                *itinerary.legs[1:],
            ]
        }
    )

    codes = _verification_codes(
        pipe_a,
        changed_itinerary,
        verified_plan.request,
    )

    assert "route_distance" in codes


@_PROPERTY_SETTINGS
@given(
    excess_soc_pct=st.floats(
        min_value=0.0001,
        max_value=20.0,
        allow_nan=False,
        allow_infinity=False,
    )
)
def test_departure_charge_above_configured_maximum_is_rejected(
    pipe_a,
    cfg_a,
    verified_plan,
    excess_soc_pct: float,
) -> None:
    """Generated departures above the configured ceiling must fail."""

    itinerary = verified_plan.alternatives[0].itinerary
    changed_stop = itinerary.stops[0].model_copy(
        update={
            "depart_soc_pct": (
                cfg_a["energy"]["charge_target_max_soc_pct"]
                + excess_soc_pct
            )
        }
    )
    changed_itinerary = itinerary.model_copy(
        update={
            "stops": [
                changed_stop,
                *itinerary.stops[1:],
            ]
        }
    )

    codes = _verification_codes(
        pipe_a,
        changed_itinerary,
        verified_plan.request,
    )

    assert "departure_soc" in codes


@_PROPERTY_SETTINGS
@given(
    required_extra_arrival_soc_pct=st.floats(
        min_value=0.01,
        max_value=20.0,
        allow_nan=False,
        allow_infinity=False,
    )
)
def test_arrival_requirement_above_recomputed_charge_is_rejected(
    pipe_a,
    verified_plan,
    required_extra_arrival_soc_pct: float,
) -> None:
    """Generated arrival requirements above the actual charge must fail."""

    itinerary = verified_plan.alternatives[0].itinerary
    stricter_request = verified_plan.request.model_copy(
        update={
            "arrival_soc_pct_min": (
                itinerary.arrival_soc_pct
                + required_extra_arrival_soc_pct
            )
        }
    )

    codes = _verification_codes(
        pipe_a,
        itinerary,
        stricter_request,
    )

    assert "arrival_soc" in codes
