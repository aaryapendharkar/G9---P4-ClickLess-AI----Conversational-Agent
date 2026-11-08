"""Tests for temporary OSRM fallback while measuring candidate detours."""

from unittest.mock import Mock

import pytest

from clickless.planning.routing import (
    OSRMNoRouteError,
    OSRMTemporaryError,
    TemporaryFailureDetourMeasurer,
)


def test_successful_osrm_candidate_detour_remains_authoritative() -> None:
    """A successful primary measurement must not invoke geometry."""

    primary_measurer = Mock()
    fallback_measurer = Mock()
    primary_measurer.detour_min.return_value = 4.5
    policy = TemporaryFailureDetourMeasurer(
        primary_measurer=primary_measurer,
        fallback_measurer=fallback_measurer,
    )

    measured_detour_min = policy.detour_min(Mock(), Mock())

    assert measured_detour_min == 4.5
    assert not policy.fallback_used
    assert policy.warning is None
    fallback_measurer.detour_min.assert_not_called()


def test_temporary_osrm_failure_uses_geometric_candidate_detour() -> None:
    """A temporary outage may use the documented deterministic estimate."""

    route = Mock()
    candidate = Mock()
    primary_measurer = Mock()
    fallback_measurer = Mock()
    primary_measurer.detour_min.side_effect = OSRMTemporaryError("OSRM timed out")
    fallback_measurer.detour_min.return_value = 7.25
    policy = TemporaryFailureDetourMeasurer(
        primary_measurer=primary_measurer,
        fallback_measurer=fallback_measurer,
    )

    measured_detour_min = policy.detour_min(route, candidate)

    assert measured_detour_min == 7.25
    assert policy.fallback_used
    assert policy.warning is not None
    assert "candidate-detour" in policy.warning
    assert "estimate" in policy.warning
    fallback_measurer.detour_min.assert_called_once_with(route, candidate)


def test_no_route_failure_does_not_use_candidate_detour_fallback() -> None:
    """An authoritative NoRoute response must remain visible."""

    primary_measurer = Mock()
    fallback_measurer = Mock()
    primary_measurer.detour_min.side_effect = OSRMNoRouteError("no road route")
    policy = TemporaryFailureDetourMeasurer(
        primary_measurer=primary_measurer,
        fallback_measurer=fallback_measurer,
    )

    with pytest.raises(OSRMNoRouteError, match="no road route"):
        policy.detour_min(Mock(), Mock())

    assert not policy.fallback_used
    assert policy.warning is None
    fallback_measurer.detour_min.assert_not_called()


def test_fallback_warning_survives_later_success_until_reset() -> None:
    """A mixed-source candidate collection must still disclose fallback."""

    primary_measurer = Mock()
    fallback_measurer = Mock()
    primary_measurer.detour_min.side_effect = [
        OSRMTemporaryError("temporary outage"),
        3.0,
    ]
    fallback_measurer.detour_min.return_value = 8.0
    policy = TemporaryFailureDetourMeasurer(
        primary_measurer=primary_measurer,
        fallback_measurer=fallback_measurer,
    )

    policy.detour_min(Mock(), Mock())
    policy.detour_min(Mock(), Mock())

    assert policy.fallback_used
    assert policy.warning is not None

    policy.reset()

    assert not policy.fallback_used
    assert policy.warning is None
