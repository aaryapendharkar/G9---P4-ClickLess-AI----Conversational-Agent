"""Metrics for validating approved Google Maps handoffs. Learning swap: Deepti/Ian.

The validator is intentionally independent of the handoff builder. It parses the public URL
format, validates coordinates and ordering, and treats a repeated segment boundary as one
physical stop when checking that each approved charging stop occurs exactly once.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

_BASE_HOST = "www.google.com"
_BASE_PATH = "/maps/dir/"
_COORDINATE_TOLERANCE = 1e-6


def _coordinate(value: str | None) -> tuple[float, float] | None:
    if value is None:
        return None
    parts = value.split(",")
    if len(parts) != 2:
        return None
    try:
        latitude, longitude = (float(part) for part in parts)
    except ValueError:
        return None
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return None
    return latitude, longitude


def _same_coordinate(left: tuple[float, float], right: tuple[float, float]) -> bool:
    return all(
        abs(left_coordinate - right_coordinate) <= _COORDINATE_TOLERANCE
        for left_coordinate, right_coordinate in zip(left, right, strict=True)
    )


def _parse_url(url: str) -> tuple[tuple[float, float], ...] | None:
    parsed_url = urlparse(url)
    if parsed_url.scheme != "https" or parsed_url.netloc != _BASE_HOST or parsed_url.path != _BASE_PATH:
        return None
    query = parse_qs(parsed_url.query, keep_blank_values=True)
    if query.get("api") != ["1"] or query.get("travelmode") != ["driving"]:
        return None
    origin = _coordinate(query.get("origin", [None])[0])
    destination = _coordinate(query.get("destination", [None])[0])
    if origin is None or destination is None:
        return None
    waypoint_text = query.get("waypoints", [""])[0]
    waypoints = []
    if waypoint_text:
        for waypoint in waypoint_text.split("|"):
            parsed_waypoint = _coordinate(waypoint)
            if parsed_waypoint is None:
                return None
            waypoints.append(parsed_waypoint)
    return (origin, *waypoints, destination)


def _normalize_segments(
    segments: list[tuple[tuple[float, float], ...]],
) -> list[tuple[float, float]]:
    route: list[tuple[float, float]] = []
    for segment_index, segment in enumerate(segments):
        start_index = 0
        if segment_index and route and _same_coordinate(route[-1], segment[0]):
            # The same physical handoff stop is both the prior destination and next origin.
            start_index = 1
        route.extend(segment[start_index:])
    return route


def check_urls(urls: list[str], expected_stops: list[tuple[float, float]]) -> dict[str, bool]:
    """Validate URL parsing, order, stop uniqueness, and segmented boundaries.

    ``expected_stops`` contains the approved charging-stop coordinates in itinerary order. A
    segmented handoff may repeat a stop as the next segment's origin; that boundary repetition is
    normalized before uniqueness and order checks. All returned values are booleans so the result
    can be consumed directly by the evaluation harness.
    """
    empty_result = {"parses": False, "ordered": False, "each_stop_once": False, "boundaries_ok": False}
    if not urls:
        return empty_result

    parsed_segments = [_parse_url(url) for url in urls]
    if any(segment is None for segment in parsed_segments):
        return empty_result
    segments = [segment for segment in parsed_segments if segment is not None]
    boundaries_ok = all(
        _same_coordinate(previous_segment[-1], current_segment[0])
        for previous_segment, current_segment in zip(segments, segments[1:], strict=False)
    )
    route = _normalize_segments(segments)

    if len(expected_stops) != len({(latitude, longitude) for latitude, longitude in expected_stops}):
        each_stop_once = False
        ordered = False
    else:
        stop_positions: list[int] = []
        for expected_stop in expected_stops:
            matching_positions = [
                index for index, actual_point in enumerate(route) if _same_coordinate(actual_point, expected_stop)
            ]
            if len(matching_positions) != 1:
                stop_positions = []
                break
            stop_positions.append(matching_positions[0])
        each_stop_once = len(stop_positions) == len(expected_stops)
        ordered = each_stop_once and stop_positions == sorted(stop_positions)

    return {
        "parses": True,
        "ordered": ordered,
        "each_stop_once": each_stop_once,
        "boundaries_ok": boundaries_ok,
    }
