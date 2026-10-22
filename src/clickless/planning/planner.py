"""Itinerary construction. Owner: Hriday.

PLACEHOLDER (walking skeleton): only direct and single-stop itineraries, charging just enough for
the next leg. TODO(Hriday):
  * multi-stop search over corridor candidates ordered by route position (DP / label-setting,
    proposal ref [12]) -- the benchmark has sparse-charging and multi-stop categories
  * return several diverse feasible plans so the trade-off layer has real alternatives
  * charge-target policy: "just enough" vs "top up while eating" (comfort profile)
  * robust feasibility run with efficiency_penalty_robust
"""

from __future__ import annotations

from typing import Any

from ..common.geo import polyline_length_km
from ..contracts import (
    Candidate,
    ChargingStop,
    Itinerary,
    Leg,
    Route,
    TripRequest,
    Vehicle,
)
from .charging_time import estimate_charge_min
from .energy import energy_kwh, kwh_to_soc_pct
from .filters import compatible_ports


def satisfied_preferences(candidate: Candidate, request: TripRequest) -> tuple[dict[str, float], list[str]]:
    """Best match score per requested preference at this stop, applying the wheelchair rule:
    only amenities tagged wheelchair=yes count when accessibility is required ("unknown" never does)."""
    scores: dict[str, float] = {}
    ids: set[str] = set()
    for pref in request.amenities:
        best = 0.0
        for na in candidate.amenities:
            if pref.wheelchair_required and na.amenity.wheelchair != "yes":
                continue
            s = na.match_scores.get(pref.key, 0.0)
            if s > 0:
                ids.add(na.amenity.amenity_id)
            best = max(best, s)
        scores[pref.key] = best
    return scores, sorted(ids)


class GreedyPlanner:
    def __init__(self, energy_cfg: dict[str, Any]) -> None:
        self.cfg = energy_cfg

    def plan(
        self, request: TripRequest, vehicle: Vehicle, route: Route, candidates: list[Candidate]
    ) -> list[Itinerary]:
        plans = []
        for stops in [[]] + [[c] for c in candidates]:
            it = self._simulate(request, vehicle, route, stops)
            if it is not None:
                plans.append(it)
        return plans

    # ------------------------------------------------------------------
    def _simulate(self, req: TripRequest, vehicle: Vehicle, route: Route, stops: list[Candidate]) -> Itinerary | None:
        cfg = self.cfg
        reserve = cfg["reserve_soc_pct"]
        arrival_req = max(req.arrival_soc_pct_min or cfg["default_arrival_soc_pct"], reserve)
        road_factor = cfg["placeholder_detour"]["road_factor"]
        scale = route.distance_km / polyline_length_km([(p.lat, p.lon) for p in route.geometry])
        min_per_km = route.duration_min / route.distance_km

        # waypoints: (label, along_road_km, offroute_road_km, detour_min, candidate)
        points = [(req.origin.text, 0.0, 0.0, 0.0, None)]
        points += [(c.station.name, c.along_route_km * scale, c.offroute_km * road_factor,
                    c.detour_min_est or 0.0, c) for c in stops]
        points += [(req.destination.text, route.distance_km, 0.0, 0.0, None)]

        soc = req.start_soc_pct
        legs: list[Leg] = []
        out_stops: list[ChargingStop] = []
        for i in range(len(points) - 1):
            a, b = points[i], points[i + 1]
            on_route = max(0.0, b[1] - a[1])
            dist = on_route + a[2] + b[2]
            drive = on_route * min_per_km + (a[3] + b[3]) / 2
            kwh = energy_kwh(dist, vehicle)
            end_soc = soc - kwh_to_soc_pct(kwh, vehicle)
            is_last = i == len(points) - 2
            if end_soc < (arrival_req if is_last else reserve):
                return None
            legs.append(Leg(start_label=a[0], end_label=b[0], distance_km=dist, drive_min=drive,
                            energy_kwh=kwh, soc_start_pct=soc, soc_end_pct=end_soc))
            soc = end_soc
            if is_last:
                break
            # charge at stop b for the next leg
            c: Candidate = b[4]
            nxt = points[i + 2]
            next_dist = max(0.0, nxt[1] - b[1]) + b[2] + nxt[2]
            next_target = arrival_req if i + 2 == len(points) - 1 else reserve
            next_drop = kwh_to_soc_pct(energy_kwh(next_dist, vehicle), vehicle)
            needed = next_target + next_drop + cfg["charge_buffer_soc_pct"]
            if needed > cfg["charge_target_max_soc_pct"]:
                return None
            depart = max(soc, needed)
            ports = compatible_ports(vehicle, c.station, req.connectors_override)
            port = max(ports, key=lambda p: p.power_kw or 0.0)
            charge_min = estimate_charge_min(vehicle, port.power_kw, port.level, soc, depart, cfg["charging_model"])
            sat, amenity_ids = satisfied_preferences(c, req)
            out_stops.append(ChargingStop(
                station_id=c.station.station_id, station_name=c.station.name, connector=port.connector,
                port_power_kw=port.power_kw, along_route_km=b[1], detour_min=b[3], arrive_soc_pct=soc,
                depart_soc_pct=depart, charge_min_est=charge_min, amenity_ids=amenity_ids,
                satisfied_preferences=sat))
            soc = depart

        sid = "+".join(s.station_id for s in out_stops) or "direct"
        return Itinerary(
            itinerary_id=f"{req.request_id}:{sid}", request_id=req.request_id, vehicle_id=vehicle.vehicle_id,
            stops=out_stops, legs=legs, total_drive_min=sum(lg.drive_min for lg in legs),
            total_charge_min=sum(s.charge_min_est for s in out_stops),
            total_detour_min=sum(s.detour_min for s in out_stops), arrival_soc_pct=soc)
