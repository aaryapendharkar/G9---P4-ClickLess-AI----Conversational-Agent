"""Tests for temporary OSRM failure fallback during route verification."""

from unittest.mock import Mock

import pytest

from clickless.planning.route_measurement import (
    ItineraryRouteMeasurement,
    TemporaryFailureFallbackMeasurer,
)
from clickless.planning.routing import (
    OSRMNoRouteError,
    OSRMResponseError,
    OSRMTemporaryError,
)


def _empty_measurement() -> ItineraryRouteMeasurement:
    """Return a minimal measurement suitable for policy-focused tests."""

    return ItineraryRouteMeasurement(
        points=(),
        legs=(),
        stop_detour_min=(),
    )


def test_successful_primary_measurement_does_not_use_fallback() -> None:
    """A working OSRM measurement remains authoritative."""

    expected_measurement = _empty_measurement()
    primary_measurer = Mock()
    fallback_measurer = Mock()
    primary_measurer.measure.return_value = expected_measurement
    policy = TemporaryFailureFallbackMeasurer(
        primary_measurer=primary_measurer,
        fallback_measurer=fallback_measurer,
    )

    returned_measurement = policy.measure(
        route=Mock(),
        stations=[],
    )

    assert returned_measurement is expected_measurement
    assert policy.selected_source == "osrm"
    assert policy.warning is None
    fallback_measurer.measure.assert_not_called()


def test_temporary_osrm_failure_uses_geometric_fallback() -> None:
    """A service outage may use the documented deterministic estimate."""

    fallback_measurement = _empty_measurement()
    primary_measurer = Mock()
    fallback_measurer = Mock()
    primary_measurer.measure.side_effect = OSRMTemporaryError("OSRM timed out")
    fallback_measurer.measure.return_value = fallback_measurement
    route = Mock()
    stations = [Mock()]
    policy = TemporaryFailureFallbackMeasurer(
        primary_measurer=primary_measurer,
        fallback_measurer=fallback_measurer,
    )

    returned_measurement = policy.measure(
        route=route,
        stations=stations,
    )

    assert returned_measurement is fallback_measurement
    assert policy.selected_source == "geometry_fallback"
    assert policy.warning is not None
    assert "OSRM" in policy.warning
    assert "estimate" in policy.warning
    fallback_measurer.measure.assert_called_once_with(route, stations)


@pytest.mark.parametrize(
    "routing_error",
    [
        OSRMNoRouteError("OSRM found no driveable route"),
        OSRMResponseError("OSRM returned malformed data"),
        ValueError("route coordinates are missing"),
    ],
)
def test_non_temporary_failure_does_not_use_fallback(
    routing_error: Exception,
) -> None:
    """Fallback must not hide invalid input or authoritative route failures."""

    primary_measurer = Mock()
    fallback_measurer = Mock()
    primary_measurer.measure.side_effect = routing_error
    policy = TemporaryFailureFallbackMeasurer(
        primary_measurer=primary_measurer,
        fallback_measurer=fallback_measurer,
    )

    with pytest.raises(type(routing_error), match=str(routing_error)):
        policy.measure(
            route=Mock(),
            stations=[],
        )

    assert policy.selected_source is None
    assert policy.warning is None
    fallback_measurer.measure.assert_not_called()


def test_each_measurement_resets_previous_fallback_state() -> None:
    """A recovered OSRM service must clear an earlier request's warning."""

    fallback_measurement = _empty_measurement()
    primary_measurement = _empty_measurement()
    primary_measurer = Mock()
    fallback_measurer = Mock()
    primary_measurer.measure.side_effect = [
        OSRMTemporaryError("temporary outage"),
        primary_measurement,
    ]
    fallback_measurer.measure.return_value = fallback_measurement
    policy = TemporaryFailureFallbackMeasurer(
        primary_measurer=primary_measurer,
        fallback_measurer=fallback_measurer,
    )

    policy.measure(
        route=Mock(),
        stations=[],
    )
    recovered_measurement = policy.measure(
        route=Mock(),
        stations=[],
    )

    assert recovered_measurement is primary_measurement
    assert policy.selected_source == "osrm"
    assert policy.warning is None
