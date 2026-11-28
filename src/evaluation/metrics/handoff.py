"""Metrics for validating approved Google Maps handoffs. Learning swap: Deepti/Ian.

The validator is intentionally independent of the handoff builder. It parses the public URL
format, validates coordinates and ordering, and treats a repeated segment boundary as one
physical stop when checking that each approved charging stop occurs exactly once.

Summarize a saved harness run (no rerun needed, #185):

    python -m evaluation.metrics.handoff results/<stamp>/A_dev_<stamp>.json

handoff_correctness() adds the checks that tie the URLs to the saved plan and to approval: the
stops are the recommended itinerary's, the URLs start and end at the request's origin and
destination, the run folder's <config>_handoff_audit.jsonl approved the item once, no saved result
is approved, and no item without status "ok" has a handoff. evaluation.report shows it per config.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

_BASE_HOST = "www.google.com"
_BASE_PATH = "/maps/dir/"
_COORDINATE_TOLERANCE = 1e-6
CHECKS = ("parses", "ordered", "each_stop_once", "boundaries_ok", "no_extra_stops")


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



def _no_extra_stops(urls: list[str], expected_count: int) -> bool:
    """True if the URLs hold nothing between origin and destination beyond the approved stops.

    check_urls() looks for each approved stop, so on its own it accepts an added waypoint.
    """
    segments = [_parse_url(url) for url in urls]
    if not segments or any(segment is None for segment in segments):
        return False
    return len(_normalize_segments(segments)) - 2 == expected_count


def _share(passed: int, total: int) -> dict[str, Any]:
    return {"passed": passed, "n": total, "rate": passed / total if total else None}


def handoff_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Handoff correctness over the rows of one saved harness run.

    The denominator is every "ok" row that saved handoff fields; a handoff that raised has no URLs
    and fails every check. Other rows have no handoff to check and are counted by status, so
    crashes and rejected requests stay visible. "ok" rows without the fields (runs saved before
    #192) are listed, not scored.
    """
    ok_rows = [row for row in rows if row.get("status") == "ok"]
    scored = [row for row in ok_rows if "handoff_urls" in row]
    passed: Counter[str] = Counter()
    n_correct = 0
    failures = []
    for row in scored:
        stops = [(stop["lat"], stop["lon"]) for stop in row["expected_stops"]]
        checks = check_urls(row["handoff_urls"], stops)
        checks["no_extra_stops"] = _no_extra_stops(row["handoff_urls"], len(stops))
        if row.get("handoff_error"):
            checks = dict.fromkeys(CHECKS, False)
        passed.update(name for name in CHECKS if checks[name])
        failed = [name for name in CHECKS if not checks[name]]
        if failed:
            failure = {"id": row.get("id"), "failed": failed}
            if row.get("handoff_error"):
                failure["error"] = row["handoff_error"]
            failures.append(failure)
        else:
            n_correct += 1
    return {
        "n_rows": len(rows),
        "rows_by_status": dict(Counter(str(row.get("status")) for row in rows)),
        "n_scored": len(scored),
        "n_handoff_errors": sum(1 for row in scored if row.get("handoff_error")),
        "unscored_ok_ids": [row.get("id") for row in ok_rows if "handoff_urls" not in row],
        "correct": _share(n_correct, len(scored)),
        "checks": {name: _share(passed[name], len(scored)) for name in CHECKS},
        "failures": failures,
    }


# --------------------------------------------------------------------------- full correctness (#185)

PLAN_CHECKS = ("matches_plan", "endpoints_ok", "approved_in_audit")
_ENDPOINT_TOLERANCE = 1e-5  # the URL carries 6 decimals; the request may carry more


def _recommended_station_ids(row: Mapping[str, Any]) -> list[str] | None:
    """Station ids of the saved result's recommended itinerary (alternatives[0]), in order."""
    result = row.get("result") or {}
    alternatives = result.get("alternatives") or []
    if not alternatives:
        return None
    return [stop["station_id"] for stop in alternatives[0]["itinerary"]["stops"]]


def _endpoints_ok(row: Mapping[str, Any]) -> bool:
    """The handoff starts at the request's origin and ends at its destination."""
    request = (row.get("result") or {}).get("request") or {}
    origin, destination = request.get("origin") or {}, request.get("destination") or {}
    segments = [_parse_url(url) for url in row.get("handoff_urls") or []]
    if not segments or any(s is None for s in segments) or None in (
        origin.get("lat"), origin.get("lon"), destination.get("lat"), destination.get("lon")
    ):
        return False
    first, last = segments[0][0], segments[-1][-1]  # type: ignore[index]
    return all(
        abs(a - b) <= _ENDPOINT_TOLERANCE
        for a, b in (
            (first[0], origin["lat"]), (first[1], origin["lon"]),
            (last[0], destination["lat"]), (last[1], destination["lon"]),
        )
    )


def handoff_correctness(
    rows: Sequence[Mapping[str, Any]], audit: Sequence[Mapping[str, Any]] | None = None
) -> dict[str, Any]:
    """handoff_metrics() plus the checks that tie the handoff to the plan and to approval (#185).

    For every scored "ok" row, on top of the URL checks (CHECKS):
      * matches_plan: the approved stops are the saved result's recommended itinerary, in order
        (station ids of alternatives[0], read from the saved PlanResult, not from the handoff builder);
      * endpoints_ok: the first URL starts at the request's origin, the last ends at its destination;
      * approved_in_audit: the run folder's approval log has exactly one approved entry for the item,
        with as many URLs as were saved (None when the log was not found).
    `fully_correct` needs all of them. Approval is also checked run-wide: no SAVED result may be
    approved or carry handoff URLs (the offline handoff approves a copy), and no item without an "ok"
    status may have handoff fields or an approval entry.
    """
    base = handoff_metrics(rows)
    by_id: dict[Any, list[Mapping[str, Any]]] = {}
    for entry in audit or []:
        by_id.setdefault(entry.get("request_id"), []).append(entry)
    scored = [row for row in rows if row.get("status") == "ok" and "handoff_urls" in row]
    url_failures = {f["id"] for f in base["failures"]}
    passed = dict.fromkeys(PLAN_CHECKS, 0)
    n_full = 0
    plan_failures = []
    for row in scored:
        expected = [stop.get("station_id") for stop in row.get("expected_stops") or []]
        checks = {
            "matches_plan": not row.get("handoff_error") and expected == _recommended_station_ids(row),
            "endpoints_ok": not row.get("handoff_error") and _endpoints_ok(row),
            "approved_in_audit": None,
        }
        if audit is not None:
            entries = by_id.get(row.get("id"), [])
            checks["approved_in_audit"] = (
                len(entries) == 1
                and entries[0].get("approved") is True
                and entries[0].get("urls") == len(row.get("handoff_urls") or [])
            )
        passed.update({k: passed[k] + 1 for k, v in checks.items() if v})
        failed = [k for k, v in checks.items() if v is False]
        if failed:
            plan_failures.append({"id": row.get("id"), "failed": failed})
        if not failed and checks["approved_in_audit"] is not False and row.get("id") not in url_failures:
            n_full += 1
    ok_ids = {row.get("id") for row in rows if row.get("status") == "ok"}
    saved_approved = [
        row.get("id")
        for row in rows
        if isinstance(row.get("result"), Mapping)
        and (row["result"].get("approved") or row["result"].get("handoff_urls"))
    ]
    non_ok = [row for row in rows if row.get("status") != "ok"]
    non_ok_handoff = sorted(
        str(row.get("id"))
        for row in non_ok
        if row.get("handoff_urls") or row.get("expected_stops") or row.get("id") in by_id
    )
    return {
        **base,
        "plan_checks": {
            k: _share(passed[k], len(scored)) if (k != "approved_in_audit" or audit is not None) else None
            for k in PLAN_CHECKS
        },
        "fully_correct": _share(n_full, len(scored)),
        "plan_failures": plan_failures,
        "approval": {
            "audit_found": audit is not None,
            "n_audit_entries": len(audit or []),
            "audit_ids_not_ok": sorted(str(i) for i in by_id if i not in ok_ids),
            "n_saved_results": sum(1 for row in rows if isinstance(row.get("result"), Mapping)),
            "saved_results_approved_or_with_urls": saved_approved,
        },
        "non_ok": {"n": len(non_ok), "with_handoff": non_ok_handoff},
    }


def read_audit(path: Path) -> list[dict[str, Any]] | None:
    """The run folder's approval log (<config>_handoff_audit.jsonl); None when it does not exist."""
    if not path.is_file():
        return None
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Handoff correctness of saved harness runs.")
    parser.add_argument("runs", nargs="+", type=Path, help="harness output JSON files")
    for path in parser.parse_args(argv).runs:
        run = json.loads(path.read_text(encoding="utf-8"))
        audit = read_audit(path.parent / f"{run.get('config')}_handoff_audit.jsonl")
        report = {"run": path.name, "config": run.get("config"), **handoff_correctness(run["rows"], audit)}
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
