"""Test which OSRM failures may use deterministic geometric fallback.

All HTTP sessions are mocked, so these tests never contact an OSRM server.
"""

from unittest.mock import Mock

import pytest
import requests

from clickless.contracts import Place
from clickless.planning.routing import (
    OSRMNoRouteError,
    OSRMResponseError,
    OSRMRouter,
    OSRMTemporaryError,
    is_geometric_fallback_allowed,
)


def _phoenix_and_flagstaff() -> tuple[Place, Place]:
    """Return places with coordinates required by OSRM routing."""

    phoenix = Place(
        text="Phoenix",
        lat=33.4484,
        lon=-112.0740,
    )
    flagstaff = Place(
        text="Flagstaff",
        lat=35.1983,
        lon=-111.6513,
    )

    return phoenix, flagstaff


def _router_with_session(
    mock_http_session: Mock,
) -> OSRMRouter:
    """Build a live router whose HTTP behavior is controlled by the test."""

    return OSRMRouter(
        base_url="https://router.project-osrm.org",
        http_session=mock_http_session,
    )


@pytest.mark.parametrize(
    "request_error",
    [
        requests.Timeout("OSRM timed out"),
        requests.ConnectionError("OSRM connection failed"),
    ],
)
def test_temporary_network_failures_allow_fallback(
    request_error,
):
    """Timeout and connection failures are temporary service problems."""

    mock_http_session = Mock()
    mock_http_session.get.side_effect = request_error
    router = _router_with_session(mock_http_session)
    origin, destination = _phoenix_and_flagstaff()

    with pytest.raises(OSRMTemporaryError) as raised_error:
        router.route(
            origin,
            destination,
        )

    assert is_geometric_fallback_allowed(raised_error.value)


def test_http_503_allows_fallback():
    """A server-side HTTP failure may use deterministic fallback."""

    mock_http_session = Mock()
    mock_http_response = Mock()
    http_error = requests.HTTPError(
        "service unavailable",
        response=mock_http_response,
    )

    mock_http_response.status_code = 503
    mock_http_response.raise_for_status.side_effect = http_error
    mock_http_session.get.return_value = mock_http_response

    router = _router_with_session(mock_http_session)
    origin, destination = _phoenix_and_flagstaff()

    with pytest.raises(OSRMTemporaryError) as raised_error:
        router.route(
            origin,
            destination,
        )

    assert is_geometric_fallback_allowed(raised_error.value)


def test_http_400_does_not_allow_fallback():
    """A rejected request must not be disguised as a geometric route."""

    mock_http_session = Mock()
    mock_http_response = Mock()
    http_error = requests.HTTPError(
        "bad request",
        response=mock_http_response,
    )

    mock_http_response.status_code = 400
    mock_http_response.raise_for_status.side_effect = http_error
    mock_http_session.get.return_value = mock_http_response

    router = _router_with_session(mock_http_session)
    origin, destination = _phoenix_and_flagstaff()

    with pytest.raises(OSRMResponseError) as raised_error:
        router.route(
            origin,
            destination,
        )

    assert not is_geometric_fallback_allowed(raised_error.value)


def test_no_route_does_not_allow_fallback():
    """An explicit NoRoute result means no road route was found."""

    mock_http_session = Mock()
    mock_http_response = Mock()

    mock_http_response.json.return_value = {
        "code": "NoRoute",
        "message": "No route found between points",
    }
    mock_http_response.url = "https://router.project-osrm.org"
    mock_http_session.get.return_value = mock_http_response

    router = _router_with_session(mock_http_session)
    origin, destination = _phoenix_and_flagstaff()

    with pytest.raises(OSRMNoRouteError) as raised_error:
        router.route(
            origin,
            destination,
        )

    assert not is_geometric_fallback_allowed(raised_error.value)


def test_invalid_json_does_not_allow_fallback():
    """Malformed service data must remain visible as a response error."""

    mock_http_session = Mock()
    mock_http_response = Mock()

    mock_http_response.json.side_effect = ValueError("invalid JSON")
    mock_http_session.get.return_value = mock_http_response

    router = _router_with_session(mock_http_session)
    origin, destination = _phoenix_and_flagstaff()

    with pytest.raises(OSRMResponseError) as raised_error:
        router.route(
            origin,
            destination,
        )

    assert not is_geometric_fallback_allowed(raised_error.value)


def test_missing_coordinates_do_not_allow_fallback():
    """Invalid caller input is not a temporary OSRM service failure."""

    router = _router_with_session(Mock())
    origin_without_coordinates = Place(text="Phoenix")
    _origin, destination = _phoenix_and_flagstaff()

    with pytest.raises(ValueError) as raised_error:
        router.route(
            origin_without_coordinates,
            destination,
        )

    assert not is_geometric_fallback_allowed(raised_error.value)
