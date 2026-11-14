"""Tests for planner hard-constraint filtering."""

from clickless.contracts import ChargerLevel, ChargingPort, Connector
from clickless.planning.filters import DefaultHardConstraintFilter, compatible_ports

SPARK = "fixture-spark-lr-2025"  # CCS1 native, no adapters
COMET = "fixture-comet-awd-2026"  # NACS native, documented CCS1 adapter
LEAFY = "fixture-leafy-2020"  # CHAdeMO + J1772 native, no adapters


def _connectors(ports):
    return [port.connector for port in ports]


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


def test_native_connector_is_compatible(store):
    spark = store.vehicle(SPARK)

    assert _connectors(compatible_ports(spark, store.station("FIX-ST-001"))) == [Connector.CCS1]


def test_adapter_makes_station_compatible(store):
    comet = store.vehicle(COMET)
    ccs_only_station = store.station("FIX-ST-001")

    assert _connectors(compatible_ports(comet, ccs_only_station)) == [Connector.CCS1]


def test_unsupported_connector_is_not_compatible(store):
    assert compatible_ports(store.vehicle(SPARK), store.station("FIX-ST-003")) == []  # NACS only
    assert compatible_ports(store.vehicle(LEAFY), store.station("FIX-ST-001")) == []  # CCS1 only


def test_undocumented_adapter_is_never_inferred(store):
    # Real CCS1 cars often have a NACS adapter, but the Spark's table row documents none.
    assert compatible_ports(store.vehicle(SPARK), store.station("FIX-ST-003")) == []
    # The Comet's documented CCS1 adapter does not extend to the CHAdeMO port at the same site.
    ports = compatible_ports(store.vehicle(COMET), store.station("FIX-ST-002"))
    assert _connectors(ports) == [Connector.CCS1]


def test_adapter_is_not_used_at_ac_ports(store):
    ccs_car_with_nacs_adapter = store.vehicle(SPARK).model_copy(update={"adapters": [Connector.NACS]})
    station = store.station("FIX-ST-004").model_copy(
        update={
            "ports": [
                ChargingPort(connector=Connector.NACS, level=ChargerLevel.L2, power_kw=11.5),
                ChargingPort(connector=Connector.NACS, level=ChargerLevel.DC_FAST, power_kw=250),
            ]
        }
    )

    ports = compatible_ports(ccs_car_with_nacs_adapter, station)

    assert [(port.connector, port.level) for port in ports] == [(Connector.NACS, ChargerLevel.DC_FAST)]


def test_native_ports_come_before_adapter_ports(store):
    comet = store.vehicle(COMET)
    # FIX-ST-004 lists CCS1 (50 kW) before NACS (250 kW).
    assert _connectors(compatible_ports(comet, store.station("FIX-ST-004"))) == [
        Connector.NACS,
        Connector.CCS1,
    ]

    equal_power_station = store.station("FIX-ST-004").model_copy(
        update={
            "ports": [
                ChargingPort(connector=Connector.CCS1, level=ChargerLevel.DC_FAST, power_kw=150),
                ChargingPort(connector=Connector.NACS, level=ChargerLevel.DC_FAST, power_kw=150),
            ]
        }
    )
    # The planner keeps the first port of maximal power, so a tie goes to the native plug.
    selected = max(compatible_ports(comet, equal_power_station), key=lambda port: port.power_kw or 0.0)
    assert selected.connector == Connector.NACS


def test_override_narrows_but_never_widens(store):
    comet, spark = store.vehicle(COMET), store.vehicle(SPARK)
    ccs_only, nacs_only = store.station("FIX-ST-001"), store.station("FIX-ST-003")

    # Driver left the adapter at home: native NACS only.
    assert compatible_ports(comet, ccs_only, [Connector.NACS]) == []
    assert _connectors(compatible_ports(comet, ccs_only, [Connector.CCS1])) == [Connector.CCS1]
    # A stated connector the vehicle table does not support is ignored.
    assert compatible_ports(spark, nacs_only, [Connector.NACS]) == []
    assert compatible_ports(spark, nacs_only, [Connector.CCS1, Connector.NACS]) == []
    # An empty override is no override.
    assert compatible_ports(comet, ccs_only, []) == compatible_ports(comet, ccs_only)


def test_filter_uses_adapter_compatibility(pipe_a, demo_text):
    request = pipe_a.parser.parse(demo_text, "filter-adapters").request
    route = pipe_a.router.route(request.origin, request.destination)
    candidates = pipe_a.retriever.candidates(route, request)
    hard_filter = DefaultHardConstraintFilter()

    def kept_and_rejected(vehicle_id):
        kept, rejected = hard_filter.apply(candidates, request, pipe_a.store.vehicle(vehicle_id))
        return (
            {c.station.station_id for c in kept},
            {c.station.station_id: reason for c, reason in rejected},
        )

    comet_kept, comet_rejected = kept_and_rejected(COMET)
    leafy_kept, leafy_rejected = kept_and_rejected(LEAFY)

    assert {"FIX-ST-001", "FIX-ST-002", "FIX-ST-003", "FIX-ST-004"} <= comet_kept
    assert comet_rejected.get("FIX-ST-005") == "connector"  # J1772 only
    assert "FIX-ST-001" not in leafy_kept
    assert leafy_rejected["FIX-ST-001"] == "connector"
