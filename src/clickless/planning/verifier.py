"""Complete-itinerary verifier: recheck the whole ordered sequence from scratch. Owner: Hriday.

It must NOT trust numbers stored in the itinerary: it recomputes energy per leg from distances
and the documented model, chains SoC itself, and re-checks each stop against the data store.

v0 checks: SoC chain / reserve / arrival requirement, connector compatibility, station exists.
TODO(Hriday): power (min_power_kw), detour (max_detour_min), station status/access, depart SoC
<= max, leg continuity (start of leg i+1 == stop i), and property-based tests (hypothesis).
"""

from __future__ import annotations

from typing import Any

from ..contracts import Itinerary, TripRequest, Vehicle, VerificationReport, Violation
from ..data import DataStore
from .energy import energy_kwh, kwh_to_soc_pct
from .filters import compatible_ports

_EPS = 1e-6


class CompleteItineraryVerifier:
    def __init__(self, store: DataStore, energy_cfg: dict[str, Any]) -> None:
        self.store = store
        self.cfg = energy_cfg

    def verify(self, itinerary: Itinerary, request: TripRequest, vehicle: Vehicle) -> VerificationReport:
        v: list[Violation] = []
        reserve = self.cfg["reserve_soc_pct"]
        arrival_req = max(request.arrival_soc_pct_min or self.cfg["default_arrival_soc_pct"], reserve)

        if len(itinerary.legs) != len(itinerary.stops) + 1:
            v.append(Violation(code="reserve", detail="legs/stops mismatch"))
            return VerificationReport(ok=False, violations=v)

        soc = request.start_soc_pct
        for i, leg in enumerate(itinerary.legs):
            soc -= kwh_to_soc_pct(energy_kwh(leg.distance_km, vehicle), vehicle)
            last = i == len(itinerary.legs) - 1
            if last and soc < arrival_req - _EPS:
                v.append(Violation(code="arrival_soc", detail=f"arrives with {soc:.1f}% < {arrival_req}%"))
            if not last:
                if soc < reserve - _EPS:
                    v.append(Violation(code="reserve", detail=f"reaches stop {i} with {soc:.1f}%", stop_index=i))
                stop = itinerary.stops[i]
                station = self.store.stations.get(stop.station_id)
                if station is None:
                    v.append(Violation(code="unknown_station", detail=stop.station_id, stop_index=i))
                elif stop.connector not in {
                    p.connector for p in compatible_ports(vehicle, station, request.connectors_override)
                }:
                    v.append(Violation(code="connector", detail=f"{stop.connector} at {stop.station_id}", stop_index=i))
                soc = max(soc, stop.depart_soc_pct)
        return VerificationReport(ok=not v, violations=v)
