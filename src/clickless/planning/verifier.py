"""Recheck a complete itinerary independently of planner control flow. Owner: Hriday.

The verifier recomputes energy use for every declared leg, chains state of charge, and reads
station constraints from the authoritative data store. It checks structure and continuity,
reserve and destination charge, station existence/status/access, connector and requested power,
departure charge limits, and the requested detour limit.

TODO(Hriday): recompute leg distances and detours from an authoritative Route instead of accepting
the planner-produced distance and detour fields. This requires a separate verifier-interface
decision. Add property-based boundary tests when Hypothesis becomes a development dependency.
"""

from __future__ import annotations

from typing import Any

from ..contracts import (
    Itinerary,
    StationStatus,
    TripRequest,
    Vehicle,
    VerificationReport,
    Violation,
)
from ..data import DataStore
from .energy import energy_kwh, kwh_to_soc_pct
from .filters import compatible_ports

# Floating-point calculations can produce values such as 19.999999% when the
# intended result is 20%. This tolerance prevents false boundary violations.
_EPSILON = 1e-6

_UNUSABLE_STATION_STATUSES = {
    StationStatus.PLANNED,
    StationStatus.TEMPORARILY_UNAVAILABLE,
}


class CompleteItineraryVerifier:
    def __init__(self, store: DataStore, energy_config: dict[str, Any]) -> None:
        self.store = store
        self.energy_config = energy_config

    def verify(
        self,
        itinerary: Itinerary,
        request: TripRequest,
        vehicle: Vehicle,
    ) -> VerificationReport:
        """Return every detected violation instead of stopping at the first one."""

        violations: list[Violation] = []
        reserve_soc_pct = self.energy_config["reserve_soc_pct"]
        minimum_arrival_soc_pct = max(
            request.arrival_soc_pct_min
            or self.energy_config["default_arrival_soc_pct"],
            reserve_soc_pct,
        )
        maximum_departure_soc_pct = self.energy_config[
            "charge_target_max_soc_pct"
        ]

        if len(itinerary.legs) != len(itinerary.stops) + 1:
            violations.append(
                Violation(
                    code="continuity",
                    detail=(
                        f"{len(itinerary.legs)} legs cannot connect "
                        f"{len(itinerary.stops)} charging stops"
                    ),
                )
            )
            return VerificationReport(ok=False, violations=violations)

        self._check_endpoint_continuity(itinerary, request, violations)

        recomputed_soc_pct = request.start_soc_pct

        for leg_index, leg in enumerate(itinerary.legs):
            recomputed_soc_pct -= kwh_to_soc_pct(
                energy_kwh(leg.distance_km, vehicle),
                vehicle,
            )
            is_destination_leg = leg_index == len(itinerary.legs) - 1

            if (
                is_destination_leg
                and recomputed_soc_pct < minimum_arrival_soc_pct - _EPSILON
            ):
                violations.append(
                    Violation(
                        code="arrival_soc",
                        detail=(
                            f"arrives with {recomputed_soc_pct:.1f}% "
                            f"< {minimum_arrival_soc_pct}%"
                        ),
                    )
                )

            if is_destination_leg:
                continue

            stop = itinerary.stops[leg_index]

            if recomputed_soc_pct < reserve_soc_pct - _EPSILON:
                violations.append(
                    Violation(
                        code="reserve",
                        detail=(
                            f"reaches stop {leg_index} with "
                            f"{recomputed_soc_pct:.1f}%"
                        ),
                        stop_index=leg_index,
                    )
                )

            # The data store is authoritative. Station details copied into the
            # planner-produced itinerary do not override stored facts.
            station = self.store.stations.get(stop.station_id)

            if station is None:
                violations.append(
                    Violation(
                        code="unknown_station",
                        detail=stop.station_id,
                        stop_index=leg_index,
                    )
                )
            else:
                if station.status in _UNUSABLE_STATION_STATUSES:
                    violations.append(
                        Violation(
                            code="status",
                            detail=(
                                f"{stop.station_id} has unusable status "
                                f"{station.status.value}"
                            ),
                            stop_index=leg_index,
                        )
                    )

                if station.access != "public":
                    violations.append(
                        Violation(
                            code="access",
                            detail=f"{stop.station_id} is not publicly accessible",
                            stop_index=leg_index,
                        )
                    )

                if stop.station_name != station.name:
                    violations.append(
                        Violation(
                            code="continuity",
                            detail=(
                                f"stop {leg_index} is named {stop.station_name!r}, "
                                f"but {stop.station_id} is {station.name!r}"
                            ),
                            stop_index=leg_index,
                        )
                    )

                compatible_charging_ports = compatible_ports(
                    vehicle,
                    station,
                    request.connectors_override,
                )
                selected_connector_ports = [
                    port
                    for port in compatible_charging_ports
                    if port.connector == stop.connector
                ]

                if not selected_connector_ports:
                    violations.append(
                        Violation(
                            code="connector",
                            detail=f"{stop.connector} at {stop.station_id}",
                            stop_index=leg_index,
                        )
                    )
                elif request.min_power_kw is not None and not any(
                    port.power_kw is not None
                    and port.power_kw >= request.min_power_kw
                    for port in selected_connector_ports
                ):
                    violations.append(
                        Violation(
                            code="power",
                            detail=(
                                f"{stop.station_id} has no selected-compatible port "
                                f"meeting {request.min_power_kw:.0f} kW"
                            ),
                            stop_index=leg_index,
                        )
                    )

            # Detour remains independent of the station checks so the report can
            # contain both a station problem and a detour violation.
            if (
                request.max_detour_min is not None
                and stop.detour_min > request.max_detour_min + _EPSILON
            ):
                violations.append(
                    Violation(
                        code="detour",
                        detail=(
                            f"{stop.detour_min:.1f} min detour exceeds "
                            f"{request.max_detour_min:.1f} min"
                        ),
                        stop_index=leg_index,
                    )
                )

            claimed_departure_soc_pct = stop.depart_soc_pct

            if claimed_departure_soc_pct < recomputed_soc_pct - _EPSILON:
                violations.append(
                    Violation(
                        code="departure_soc",
                        detail=(
                            f"departure {claimed_departure_soc_pct:.1f}% is below "
                            f"recomputed arrival {recomputed_soc_pct:.1f}%"
                        ),
                        stop_index=leg_index,
                    )
                )

            if claimed_departure_soc_pct > maximum_departure_soc_pct + _EPSILON:
                violations.append(
                    Violation(
                        code="departure_soc",
                        detail=(
                            f"departure {claimed_departure_soc_pct:.1f}% exceeds "
                            f"configured maximum {maximum_departure_soc_pct:.1f}%"
                        ),
                        stop_index=leg_index,
                    )
                )

            # Continue checking later legs without granting impossible energy.
            recomputed_soc_pct = min(
                max(recomputed_soc_pct, claimed_departure_soc_pct),
                maximum_departure_soc_pct,
            )

        return VerificationReport(ok=not violations, violations=violations)

    def _check_endpoint_continuity(
        self,
        itinerary: Itinerary,
        request: TripRequest,
        violations: list[Violation],
    ) -> None:
        """Check the declared origin, destination, and ordered stop labels."""

        first_leg = itinerary.legs[0]
        last_leg = itinerary.legs[-1]

        if first_leg.start_label != request.origin.text:
            violations.append(
                Violation(
                    code="continuity",
                    detail=(
                        f"first leg starts at {first_leg.start_label!r}, "
                        f"expected {request.origin.text!r}"
                    ),
                )
            )

        if last_leg.end_label != request.destination.text:
            violations.append(
                Violation(
                    code="continuity",
                    detail=(
                        f"last leg ends at {last_leg.end_label!r}, "
                        f"expected {request.destination.text!r}"
                    ),
                )
            )

        for stop_index, stop in enumerate(itinerary.stops):
            inbound_leg = itinerary.legs[stop_index]
            outbound_leg = itinerary.legs[stop_index + 1]

            if inbound_leg.end_label != stop.station_name:
                violations.append(
                    Violation(
                        code="continuity",
                        detail=(
                            f"leg {stop_index} ends at {inbound_leg.end_label!r}, "
                            f"expected stop {stop.station_name!r}"
                        ),
                        stop_index=stop_index,
                    )
                )

            if outbound_leg.start_label != stop.station_name:
                violations.append(
                    Violation(
                        code="continuity",
                        detail=(
                            f"leg {stop_index + 1} starts at "
                            f"{outbound_leg.start_label!r}, expected stop "
                            f"{stop.station_name!r}"
                        ),
                        stop_index=stop_index,
                    )
                )
