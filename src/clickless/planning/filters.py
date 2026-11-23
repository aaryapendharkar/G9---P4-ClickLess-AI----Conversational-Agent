"""Hard-constraint filters (connector, power, detour, status, access). Owner: Hriday.

A candidate that fails any hard constraint is removed with a reason string; reasons feed the
failure analysis ("compatibility errors", ...) and the explanation of why a station was skipped.
"""

from __future__ import annotations

from ..contracts import (
    Candidate,
    ChargerLevel,
    ChargingPort,
    Connector,
    Station,
    StationStatus,
    TripRequest,
    Vehicle,
)

_UNUSABLE_STATUS = {StationStatus.TEMPORARILY_UNAVAILABLE, StationStatus.PLANNED}

# Levels at which a documented adapter is trusted. Every adapter in data/curated/vehicles.csv is
# cited to a DC fast-charging source (Tesla's CCS1 adapter, manufacturers' NACS DC adapters,
# ADR 0009), and the Vehicle contract records no level for an adapter.
_ADAPTER_LEVELS = frozenset({ChargerLevel.DC_FAST})


def compatible_ports(vehicle: Vehicle, station: Station, override: list[Connector] | None = None) -> list[ChargingPort]:
    """Ports on `station` the vehicle can physically use, native ports first.

    Compatibility comes only from the curated vehicle table (conservative: we may miss a valid
    stop but never propose an unusable one):

    * a native connector matches a port with that connector at any level;
    * a documented adapter (`vehicle.adapters`) matches a port with that connector at DC fast
      level only. Using an adapter on AC (e.g. a CCS1 car with a NACS DC adapter at a Tesla
      destination charger) is not documented, so it is never inferred;
    * nothing else is inferred -- not an adapter that is sold for the car but missing from the
      table, nor one implied by another connector.

    `override` (`TripRequest.connectors_override`, connectors the driver stated) can only narrow
    this set, never widen it: a stated connector the vehicle table does not support is ignored, so
    a parsing mistake cannot make an unusable station look compatible. "I left my adapter at home"
    becomes an override with only the native connector. `None` or `[]` means no override.

    Native ports come before adapter ports (station order within each group). Callers that pick
    the first of several equally powerful ports therefore never send the driver to an adapter
    port when a native port is just as fast.
    """
    allowed = set(override) if override else None
    native_ports: list[ChargingPort] = []
    adapter_ports: list[ChargingPort] = []
    for port in station.ports:
        if allowed is not None and port.connector not in allowed:
            continue
        if port.connector in vehicle.native_connectors:
            native_ports.append(port)
        elif port.connector in vehicle.adapters and port.level in _ADAPTER_LEVELS:
            adapter_ports.append(port)
    return native_ports + adapter_ports


class DefaultHardConstraintFilter:
    def station_rejection(self, station: Station, request: TripRequest, vehicle: Vehicle) -> str | None:
        """The reason `station` can never be a stop for this request and vehicle, or None.

        The station-level hard constraints (status, access, connector, power), in the order apply()
        reports them. Retrieval uses this through the pipeline (#106) so that only usable stations
        compete for the capped candidate slots; the detour check needs a measured candidate and
        stays in apply().
        """
        ports = compatible_ports(vehicle, station, request.connectors_override)
        if station.status in _UNUSABLE_STATUS:
            return f"status:{station.status.value}"
        if station.access != "public":
            return "access:restricted"
        if not ports:
            return "connector"
        if request.min_power_kw and not any((p.power_kw or 0) >= request.min_power_kw for p in ports):
            return "power"
        return None

    def apply(
        self, candidates: list[Candidate], request: TripRequest, vehicle: Vehicle
    ) -> tuple[list[Candidate], list[tuple[Candidate, str]]]:
        kept: list[Candidate] = []
        rejected: list[tuple[Candidate, str]] = []
        for c in candidates:
            reason = self.station_rejection(c.station, request, vehicle)
            if reason is not None:
                rejected.append((c, reason))
            elif (
                request.max_detour_min is not None
                and c.detour_min_est is not None
                and c.detour_min_est > request.max_detour_min
            ):
                rejected.append((c, "detour"))
            else:
                kept.append(c)
        return kept, rejected
