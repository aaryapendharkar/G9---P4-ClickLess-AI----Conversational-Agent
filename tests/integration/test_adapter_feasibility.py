"""Documented adapters reach planning and verification, not only the filter."""

from clickless.contracts import Connector


def test_documented_adapter_makes_trip_feasible(pipe_a):
    # With 25% the Comet cannot reach the first NACS station (Cordes Junction); its documented
    # CCS1 adapter lets it charge at the CCS1-only Anthem station instead.
    result = pipe_a.run("Phoenix to Flagstaff in my comet with 25%", request_id="adapter-1")

    assert result.status == "ok"
    first_stop = result.alternatives[0].itinerary.stops[0]
    assert (first_stop.station_id, first_stop.connector) == ("FIX-ST-001", Connector.CCS1)
    assert result.verification is not None and result.verification.ok
    assert not any(note == "skipped FIX-ST-001: connector" for note in result.notes)
