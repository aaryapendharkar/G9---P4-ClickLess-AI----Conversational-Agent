"""Hard-constraint filters (connector, power, detour, status, access). Owner: Hriday.

A candidate that fails any hard constraint is removed with a reason string; reasons feed the
failure analysis ("compatibility errors", ...) and the explanation of why a station was skipped.
"""

from __future__ import annotations

from ..contracts import (
    Candidate,
    ChargingPort,
    Connector,
    Station,
    StationStatus,
    TripRequest,
    Vehicle,
)

_UNUSABLE_STATUS = {StationStatus.TEMPORARILY_UNAVAILABLE, StationStatus.PLANNED}


def compatible_ports(
    vehicle: Vehicle, station: Station, override: list[Connector] | None = None
) -> list[ChargingPort]:
    """Ports on `station` the vehicle can physically use.

    PLACEHOLDER: native connectors only -- documented adapters are ignored (conservative: we may
    miss valid stops but never propose an unusable one).
    LEARNING SWAP (Manas): include vehicle.adapters, decide how `override` interacts with the
    vehicle table, and add tests (NACS car + CCS adapter at a CCS-only station, etc.).
    """
    allowed = set(override) if override else set(vehicle.native_connectors)
    return [p for p in station.ports if p.connector in allowed]


class DefaultHardConstraintFilter:
    def apply(
        self, candidates: list[Candidate], request: TripRequest, vehicle: Vehicle
    ) -> tuple[list[Candidate], list[tuple[Candidate, str]]]:
        kept: list[Candidate] = []
        rejected: list[tuple[Candidate, str]] = []
        for c in candidates:
            st = c.station
            ports = compatible_ports(vehicle, st, request.connectors_override)
            if st.status in _UNUSABLE_STATUS:
                rejected.append((c, f"status:{st.status.value}"))
            elif st.access != "public":
                rejected.append((c, "access:restricted"))
            elif not ports:
                rejected.append((c, "connector"))
            elif request.min_power_kw and not any(
                (p.power_kw or 0) >= request.min_power_kw for p in ports
            ):
                rejected.append((c, "power"))
            elif (
                request.max_detour_min is not None
                and c.detour_min_est is not None
                and c.detour_min_est > request.max_detour_min
            ):
                rejected.append((c, "detour"))
            else:
                kept.append(c)
        return kept, rejected
