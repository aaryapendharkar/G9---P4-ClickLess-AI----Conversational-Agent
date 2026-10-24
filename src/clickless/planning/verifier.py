"""Complete-itinerary verifier: recheck the whole ordered sequence from scratch. Owner: Hriday.

It must NOT trust numbers stored in the itinerary: it recomputes energy per leg from distances
and the documented model, chains SoC itself, and re-checks each stop against the data store.

Current checks: SoC chain, reserve, arrival requirement, station existence, connector
compatibility, minimum charging power, and maximum detour.

TODO(Hriday): station status/access, departure SoC <= configured maximum, leg continuity
(start of leg i+1 == stop i), and property-based tests with Hypothesis.
"""

from __future__ import annotations

from typing import Any

from ..contracts import Itinerary, TripRequest, Vehicle, VerificationReport, Violation
from ..data import DataStore
from .energy import energy_kwh, kwh_to_soc_pct
from .filters import compatible_ports

# Small tolerance for floating-point comparisons. For example, a computed
# 20% battery value may internally be represented as 19.999999%.
_EPSILON = 1e-6


class CompleteItineraryVerifier:
    def __init__(self, store: DataStore, energy_cfg: dict[str, Any]) -> None:
        self.store = store
        self.energy_config = energy_cfg

    def verify(
        self, itinerary: Itinerary, request: TripRequest, vehicle: Vehicle
    ) -> VerificationReport:
        violations: list[Violation] = []
        reserve = self.energy_config["reserve_soc_pct"]
        arrival_req = max(
            request.arrival_soc_pct_min
            or self.energy_config["default_arrival_soc_pct"],
            reserve,
        )

        if len(itinerary.legs) != len(itinerary.stops) + 1:
            violations.append(Violation(code="reserve", detail="legs/stops mismatch"))
            return VerificationReport(ok=False, violations=violations)

        # pct = percentage
        state_of_charge_pct = request.start_soc_pct
        for leg_index, leg in enumerate(itinerary.legs):

            state_of_charge_pct -= kwh_to_soc_pct(
                energy_kwh(leg.distance_km, vehicle), vehicle
            )
            is_last_leg = leg_index == len(itinerary.legs) - 1

            if is_last_leg and state_of_charge_pct < arrival_req - _EPSILON:
                violations.append(
                    Violation(
                        code="arrival_soc",
                        detail=f"arrives with {state_of_charge_pct:.1f}% < {arrival_req}%",
                    )
                )

            if not is_last_leg:
                if state_of_charge_pct < reserve - _EPSILON:
                    violations.append(
                        Violation(
                            code="reserve",
                            detail=f"reaches stop {leg_index} with {state_of_charge_pct:.1f}%",
                            stop_index=leg_index,
                        )
                    )

                stop = itinerary.stops[leg_index]
                # The data store is authoritative. Do not trust station details 
                # copied into an itinerary produced by the planner.
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
                    ports = compatible_ports(
                        vehicle,
                        station,
                        request.connectors_override,
                    )

                    # Restrict validation to compatible ports using the connector selected for this charging stop.
                    selected_ports = [
                        port for port in ports if port.connector == stop.connector
                    ]

                    if not selected_ports:
                        violations.append(
                            Violation(
                                code="connector",
                                detail=f"{stop.connector} at {stop.station_id}",
                                stop_index=leg_index,
                            )
                        )
                    # Unknown power cannot satisfy an explicit minimum-power requirement.
                    elif request.min_power_kw is not None and not any(
                        port.power_kw is not None
                        and port.power_kw >= request.min_power_kw
                        for port in selected_ports
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

                # Detour is checked independently so the report may contain both a
                # station/connector problem and a detour violation.
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

                state_of_charge_pct = max(state_of_charge_pct, stop.depart_soc_pct)

        return VerificationReport(ok=not violations, violations=violations)
