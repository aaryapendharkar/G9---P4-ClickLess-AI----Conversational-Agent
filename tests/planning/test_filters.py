"""Tests for planner hard-constraint filtering."""

import pytest

from clickless.planning.filters import compatible_ports


def test_filter_reasons(pipe_a, demo_text):
    request = pipe_a.parser.parse(demo_text, "filter-reasons").request
    vehicle = pipe_a.store.vehicle(request.vehicle_id)
    route = pipe_a.router.route(request.origin, request.destination)
    candidates = pipe_a.retriever.candidates(route, request)

    _accepted, rejected = pipe_a.filter.apply(
        candidates,
        request,
        vehicle,
    )
    reasons_by_station_id = {
        candidate.station.station_id: reason
        for candidate, reason in rejected
    }

    assert reasons_by_station_id["FIX-ST-003"] == "connector"
    assert reasons_by_station_id["FIX-ST-007"].startswith("status")
    assert reasons_by_station_id["FIX-ST-008"].startswith("access")


@pytest.mark.xfail(reason="TODO(Manas, learning swap): documented adapters")
def test_adapter_makes_station_compatible(store):
    comet = store.vehicle("fixture-comet-awd-2026")
    ccs_only_station = store.station("FIX-ST-001")

    assert compatible_ports(comet, ccs_only_station)
