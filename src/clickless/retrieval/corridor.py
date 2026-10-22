"""Route-corridor retrieval. Owner: Manas.

PLACEHOLDER: brute-force distance from every station to the route polyline. Fine for fixtures,
too slow for ~70k US stations x 150 requests. TODO(Manas):
  * spatial index (sklearn BallTree with haversine metric, or an R-tree) built once per snapshot
  * prefilter by route bounding box + buffer, then exact point-to-segment distance
  * amenity linking: precompute Station NEAR Amenity (<= 400 m) once in the KG / an index
"""

from __future__ import annotations

from ..common.geo import haversine_km, nearest_on_polyline
from ..contracts import Candidate, NearbyAmenity, Route, TripRequest
from ..data import DataStore


class CorridorRetriever:
    def __init__(self, store: DataStore, buffer_km: float, amenity_radius_m: float, max_candidates: int):
        self.store = store
        self.buffer_km = buffer_km
        self.amenity_radius_m = amenity_radius_m
        self.max_candidates = max_candidates

    def candidates(self, route: Route, request: TripRequest) -> list[Candidate]:
        poly = [(p.lat, p.lon) for p in route.geometry]
        out: list[Candidate] = []
        for st in self.store.stations.values():
            off_km, seg, along = nearest_on_polyline(st.lat, st.lon, poly)
            if off_km > self.buffer_km:
                continue
            nearby = [
                NearbyAmenity(amenity=a, distance_m=d)
                for a in self.store.amenities
                if (d := haversine_km(st.lat, st.lon, a.lat, a.lon) * 1000) <= self.amenity_radius_m
            ]
            out.append(
                Candidate(station=st, route_index=seg, along_route_km=along, offroute_km=off_km, amenities=nearby)
            )
        out.sort(key=lambda c: c.along_route_km)
        return out[: self.max_candidates]
