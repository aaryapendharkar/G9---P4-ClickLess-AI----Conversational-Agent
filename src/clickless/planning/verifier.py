"""Recheck a complete itinerary independently of planner control flow. Owner: Hriday.

The verifier recomputes energy use for every declared leg, chains state of charge, and reads
station constraints from the authoritative data store. It checks structure and continuity,
reserve and destination charge, station existence/status/access, connector and requested power,
departure charge limits, and the requested detour limit.

Configuration A keeps deterministic geometric measurements. Runtime OSRM mode
injects cache-backed waypoint measurements. Deterministic property-based tests
exercise distance tolerance, departure ceilings, and arrival-charge boundaries.
"""

from __future__ import annotations

from typing import Any

from ..contracts import (
    Itinerary,
    Route,
    Station,
    StationStatus,
    TripRequest,
    Vehicle,
    VerificationReport,
    Violation,
)
from ..data import DataStore
from .energy import energy_kwh, kwh_to_soc_pct
from .filters import compatible_ports
from .route_measurement import (
    GeometricRouteMeasurer,
    ItineraryRouteMeasurement,
    ItineraryRouteMeasurer,
)

# Floating-point calculations can produce values such as 19.999999% when the
# intended result is 20%. This tolerance prevents false boundary violations.
_EPSILON = 1e-6

# Cached route geometry is approximate, so route comparisons use human-scale
# tolerances rather than floating-point equality.
_ROUTE_DISTANCE_TOLERANCE_KM = 0.1
_DETOUR_TOLERANCE_MIN = 0.1

_UNUSABLE_STATION_STATUSES = {
    StationStatus.PLANNED,
    StationStatus.TEMPORARILY_UNAVAILABLE,
}


class CompleteItineraryVerifier:
    def __init__(
        self,
        store: DataStore,
        energy_config: dict[str, Any],
        route_measurer: ItineraryRouteMeasurer | None = None,
    ) -> None:
        self.store = store
        self.energy_config = energy_config
        # Configuration A keeps the existing deterministic estimator. Runtime
        # OSRM configurations can inject a cache-backed strategy without
        # changing verifier logic or trusting values produced by the planner.
        self.route_measurer = route_measurer or GeometricRouteMeasurer(
            energy_config
        )

    def verify(
        self,
        itinerary: Itinerary,
        request: TripRequest,
        vehicle: Vehicle,
        route: Route,
    ) -> VerificationReport:
        """Return every detected violation instead of stopping at the first one."""

        violations: list[Violation] = []
        reserve_soc_pct = self.energy_config["reserve_soc_pct"]
        minimum_arrival_soc_pct = max(
            request.arrival_soc_pct_min
            or self.energy_config["default_arrival_soc_pct"],
            reserve_soc_pct,
        )
        maximum_departure_soc_pct = self.energy_config["charge_target_max_soc_pct"]

        # Phase 1: reject a malformed skeleton before indexing legs and stops.
        # A valid trip always has one more leg than charging stops:
        # origin -> stop(s) -> destination.
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

        # Phase 2: resolve planner-provided station IDs through the authoritative
        # data store. A None value means that the itinerary references a
        # station that does not exist in our dataset.
        resolved_stations: list[Station | None] = [
            self.store.stations.get(stop.station_id) for stop in itinerary.stops
        ]

        for stop_index, station in enumerate(resolved_stations):
            if station is None:
                violations.append(
                    Violation(
                        code="unknown_station",
                        detail=itinerary.stops[stop_index].station_id,
                        stop_index=stop_index,
                    )
                )

        # Phase 3: independently rebuild the route measurements. Measurements
        # require coordinates for every charging station.
        # An unknown station already makes the itinerary invalid, so in that
        # case we skip route measurement but continue looking for other errors.
        route_measurement: ItineraryRouteMeasurement | None = None

        if all(station is not None for station in resolved_stations):
            known_stations = [
                station for station in resolved_stations if station is not None
            ]

            try:
                route_measurement = self.route_measurer.measure(
                    route,
                    known_stations,
                )
            except ValueError as error:
                # A ValueError means the station sequence cannot form a valid
                # forward path along the supplied route.
                violations.append(
                    Violation(
                        code="continuity",
                        detail=str(error),
                    )
                )

        # Phase 4: confirm that the declared labels form one continuous trip,
        # then compare the planner's numerical claims with our measurements.
        self._check_endpoint_continuity(
            itinerary,
            request,
            violations,
        )

        if route_measurement is not None:
            self._check_route_claims(
                itinerary,
                route_measurement,
                request,
                violations,
            )

        # Phase 5: replay the trip leg by leg. State of charge flows from one
        # leg into the next, with charging applied only at declared stops.
        recomputed_soc_pct = request.start_soc_pct

        for leg_index, leg in enumerate(itinerary.legs):
            if route_measurement is None:
                # Unknown or invalid station data already makes the itinerary
                # fail. Using the declared distance here lets verification
                # continue and collect additional useful violations.
                verified_leg_distance_km = leg.distance_km
            else:
                # For a structurally valid itinerary, energy must be calculated
                # from the independent route measurement—not from a distance
                # supplied by the planner.
                verified_leg_distance_km = route_measurement.legs[
                    leg_index
                ].distance_km

            recomputed_soc_pct -= kwh_to_soc_pct(
                energy_kwh(
                    verified_leg_distance_km,
                    vehicle,
                ),
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

            # resolved_stations preserves the same order as itinerary.stops.
            # Station facts come from the data store, never from planner output.
            station = resolved_stations[leg_index]

            if station is not None:
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

        # Reaching this point means every leg and stop was inspected. The report
        # succeeds only when no phase above recorded a violation.
        return VerificationReport(ok=not violations, violations=violations)

    def _check_route_claims(
        self,
        itinerary: Itinerary,
        route_measurement: ItineraryRouteMeasurement,
        request: TripRequest,
        violations: list[Violation],
    ) -> None:
        """Compare planner route claims with independent measurements."""

        # First compare each leg because understated distance can make an
        # otherwise impossible battery calculation appear feasible.
        for leg_index, (declared_leg, measured_leg) in enumerate(
            zip(
                itinerary.legs,
                route_measurement.legs,
                strict=True,
            )
        ):
            distance_difference_km = abs(
                declared_leg.distance_km - measured_leg.distance_km
            )

            if distance_difference_km > _ROUTE_DISTANCE_TOLERANCE_KM:
                violations.append(
                    Violation(
                        code="route_distance",
                        detail=(
                            f"leg {leg_index} declares "
                            f"{declared_leg.distance_km:.1f} km, but the "
                            f"authoritative route measurement is "
                            f"{measured_leg.distance_km:.1f} km"
                        ),
                    )
                )

        # Then compare each stop's detour and enforce the driver's limit using
        # our measurement rather than the planner's declared value.
        for stop_index, (stop, measured_detour_min) in enumerate(
            zip(
                itinerary.stops,
                route_measurement.stop_detour_min,
                strict=True,
            )
        ):
            detour_difference_min = abs(
                stop.detour_min - measured_detour_min
            )

            if detour_difference_min > _DETOUR_TOLERANCE_MIN:
                violations.append(
                    Violation(
                        code="detour",
                        detail=(
                            f"stop {stop_index} declares a "
                            f"{stop.detour_min:.1f} min detour, but the "
                            f"authoritative estimate is "
                            f"{measured_detour_min:.1f} min"
                        ),
                        stop_index=stop_index,
                    )
                )

            # The driver's limit must be applied to the independently measured
            # detour, not to the value supplied by the planner.
            if (
                request.max_detour_min is not None
                and measured_detour_min
                > request.max_detour_min + _DETOUR_TOLERANCE_MIN
            ):
                violations.append(
                    Violation(
                        code="detour",
                        detail=(
                            f"{measured_detour_min:.1f} min authoritative "
                            f"detour exceeds the requested maximum of "
                            f"{request.max_detour_min:.1f} min"
                        ),
                        stop_index=stop_index,
                    )
                )

        # Finally check the itinerary-level total because ranking and
        # explanations consume this aggregate rather than individual stops.
        declared_total_detour_min = itinerary.total_detour_min
        measured_total_detour_min = route_measurement.total_detour_min

        if (
            abs(
                declared_total_detour_min
                - measured_total_detour_min
            )
            > _DETOUR_TOLERANCE_MIN
        ):
            violations.append(
                Violation(
                    code="detour",
                    detail=(
                        f"itinerary declares "
                        f"{declared_total_detour_min:.1f} total detour "
                        f"minutes, but the authoritative estimate is "
                        f"{measured_total_detour_min:.1f}"
                    ),
                )
            )

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
