"""Hand-built worlds with known answers for the oracle tests.

The route runs along the equator from (0, 0) eastwards, so a station at lon = km / KM_PER_DEG sits
exactly `km` along the route and a latitude offset of off_km / KM_PER_DEG puts it exactly off_km
off-route. Vehicle T: 50 kWh usable, 0.2 kWh/km -> 1 % SoC = 2.5 km; max DC 80 kW; CCS1 native,
NACS adapter. Detour model: 2 min + 2 x off x 1.3 / 40 km/h -> 3.9 min per off-route km.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path

from clickless.contracts import (
    Alternative,
    ChargingPort,
    ChargingStop,
    Itinerary,
    LatLon,
    Place,
    PlanCost,
    PlanResult,
    Provenance,
    Route,
    Station,
    TripRequest,
    Vehicle,
)
from clickless.data.loaders import DataStore
from evaluation.oracle.label_setting import LabelSettingOracle, OracleConfig, OraclePlan

KM_PER_DEG = 2 * math.pi * 6371.0088 / 360
PROV = Provenance(source="SYNTHETIC", retrieved_at=datetime(2026, 1, 1, tzinfo=timezone.utc), status="cached")

# The documented values of configs/energy.yaml, pinned so the known answers below stay valid.
ENERGY = {
    "reserve_soc_pct": 10,
    "default_arrival_soc_pct": 20,
    "efficiency_penalty_robust": 0.15,
    "charge_target_max_soc_pct": 100,
    "charge_buffer_soc_pct": 2,
    "charging_model": {
        "taper_start_soc_pct": 80,
        "taper_power_fraction": 0.4,
        "session_overhead_min": 3,
        "ac_efficiency": 0.9,
        "unknown_power_kw": {"dc_fast": 50.0, "l2": 6.6, "l1": 1.4},
    },
    "placeholder_detour": {"road_factor": 1.3, "offroute_speed_kmh": 40, "fixed_min": 2},
}


def vehicle(vid="t", usable=50.0, eff=0.2, native=("CCS1",), adapters=("NACS",), max_dc=80.0) -> Vehicle:
    return Vehicle(
        vehicle_id=vid,
        make="Test",
        model=vid,
        model_year=2024,
        usable_kwh=usable,
        efficiency_kwh_per_km=eff,
        native_connectors=list(native),
        adapters=list(adapters),
        max_dc_kw=max_dc,
    )


def port(connector="CCS1", level="dc_fast", kw: float | None = 150.0) -> ChargingPort:
    return ChargingPort(connector=connector, level=level, power_kw=kw)


def station(sid, km, off_km=0.0, ports=None, status="available", access="public") -> Station:
    return Station(
        station_id=sid,
        name=sid,
        lat=off_km / KM_PER_DEG,
        lon=km / KM_PER_DEG,
        access=access,
        status=status,
        ports=ports or [port()],
        provenance=PROV,
    )


def off_for_detour(minutes: float) -> float:
    """Off-route km that gives this placeholder detour."""
    return (minutes - 2.0) / 3.9


def route(km=200.0, duration=120.0) -> Route:
    end = LatLon(lat=0.0, lon=km / KM_PER_DEG)
    return Route(
        route_id="test",
        origin=LatLon(lat=0.0, lon=0.0),
        destination=end,
        geometry=[LatLon(lat=0.0, lon=0.0), end],
        distance_km=km,
        duration_min=duration,
        provenance=PROV,
    )


class TwinOracle:
    """Runs every query on the indexed oracle (twice: fresh, then from its caches) and on the
    exhaustive reference, asserts the three answers are identical, and returns the indexed one.
    Every hand-built oracle test therefore also checks that the fast path changes no result."""

    COMPARED = ("is_feasible", "pareto_plans", "check_itinerary", "is_valid", "check_plan_result")

    def __init__(self, fast: LabelSettingOracle, slow: LabelSettingOracle) -> None:
        assert fast.cfg.method == "indexed" and slow.cfg.method == "exhaustive"
        self.fast, self.slow = fast, slow

    def __getattr__(self, name):
        attr = getattr(self.fast, name)
        if name not in self.COMPARED:
            return attr

        def both(*args, **kwargs):
            runs = [_outcome(getattr(o, name), args, kwargs) for o in (self.slow, self.fast, self.fast)]
            answers = [(kind, (type(v), str(v)) if kind == "raised" else v) for kind, v in runs]
            assert answers[1] == answers[0], f"{name}: indexed {answers[1]!r} != exhaustive {answers[0]!r}"
            assert answers[2] == answers[0], f"{name}: cached indexed {answers[2]!r} != exhaustive {answers[0]!r}"
            kind, value = runs[1]
            if kind == "raised":
                raise value
            return value

        return both

    def with_overrides(self, **overrides) -> TwinOracle:
        return TwinOracle(self.fast.with_overrides(**overrides), self.slow.with_overrides(**overrides))


def _outcome(fn, args, kwargs):
    try:
        return ("returned", fn(*args, **kwargs))
    except Exception as e:  # compared by type and message
        return ("raised", e)


def world(stations=(), vehicles=None, route_km=200.0, **cfg) -> TwinOracle:
    r = route(route_km)
    store = DataStore(
        list(stations), vehicles or [vehicle()], [], {"a": (0.0, 0.0), "b": (0.0, route_km / KM_PER_DEG)}, Path(".")
    )

    def make(method):
        return LabelSettingOracle(store, OracleConfig(energy=ENERGY, method=method, **cfg), route_source=lambda o, d: r)

    return TwinOracle(make("indexed"), make("exhaustive"))


def req(start=50.0, route_km=200.0, vehicle_id="t", **kw) -> TripRequest:
    return TripRequest(
        request_id="r",
        raw_text="",
        origin=Place(text="a", lat=0.0, lon=0.0),
        destination=Place(text="b", lat=0.0, lon=route_km / KM_PER_DEG),
        vehicle_id=vehicle_id,
        start_soc_pct=start,
        **kw,
    )


def stop(sid, connector="CCS1", kw: float | None = 50.0, km=60.0, detour=2.0, arrive=26.0, depart=80.0) -> ChargingStop:
    return ChargingStop(
        station_id=sid,
        station_name=sid,
        connector=connector,
        port_power_kw=kw,
        along_route_km=km,
        detour_min=detour,
        arrive_soc_pct=arrive,
        depart_soc_pct=depart,
        charge_min_est=10.0,
    )


def itinerary(*stops: ChargingStop, vehicle_id="t") -> Itinerary:
    return Itinerary(
        itinerary_id="i",
        request_id="r",
        vehicle_id=vehicle_id,
        stops=list(stops),
        legs=[],
        total_drive_min=120.0,
        total_charge_min=0.0,
        total_detour_min=sum(s.detour_min for s in stops),
        arrival_soc_pct=20.0,
    )


def from_plan(plan: OraclePlan) -> Itinerary:
    return itinerary(
        *(
            stop(
                s.station_id, s.connector, s.port_power_kw, s.along_km, s.detour_min, s.arrive_soc_pct, s.depart_soc_pct
            )
            for s in plan.stops
        )
    )


def result(request_id, status="ok", itins=(), verification=None) -> PlanResult:
    cost = PlanCost(
        profile="balanced",
        drive_min=0,
        charge_min=0,
        detour_penalty_min=0,
        stop_overhead_min=0,
        amenity_credit_min=0,
        evidence_credit_min=0,
        total_min=0,
        amenity_satisfaction=1.0,
    )
    alts = [
        Alternative(label="Recommended" if k == 0 else "Fastest", itinerary=it, cost=cost) for k, it in enumerate(itins)
    ]
    return PlanResult(request_id=request_id, config="A", status=status, alternatives=alts, verification=verification)
