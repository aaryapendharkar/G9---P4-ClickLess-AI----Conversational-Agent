"""Expected answers of CQ10-CQ20 on the fixture KG (#158; Aarya on Manas's behalf).

Same approach as test_competency_query_answers.py: every query runs unchanged through the shared
loader, and cases the fixtures lack are built by copying fixture records and rebuilding the graph,
so they also go through the KG builder's conventions.
"""

from datetime import timedelta

import pytest

from clickless.common.geo import haversine_km
from clickless.contracts import ChargerLevel, Connector, DataStatus, StationStatus
from clickless.data import DataStore
from clickless.data.provenance import is_stale, stale_cutoff, status_label
from clickless.retrieval.kg.build import amenity_uri, build_graph, port_uri, station_uri, vehicle_uri
from clickless.retrieval.kg.competency import run_competency_query

pytest.importorskip("rdflib")  # CI installs the kg extra; this only skips on a machine without it

SPARK, COMET, LEAFY = "fixture-spark-lr-2025", "fixture-comet-awd-2026", "fixture-leafy-2020"


@pytest.fixture(scope="module")
def graph(store):
    return build_graph(store)


def _rebuilt(store, stations=None, vehicles=None, amenities=None, extra_amenities=()):
    return build_graph(
        DataStore(
            [(stations or {}).get(sid, st) for sid, st in store.stations.items()],
            [(vehicles or {}).get(vid, v) for vid, v in store.vehicles.items()],
            [(amenities or {}).get(a.amenity_id, a) for a in store.amenities] + list(extra_amenities),
            store.places,
            store.routes_dir,
        )
    )


def _py(term):
    return None if term is None else term.toPython()


def _station(store, sid, **update):
    return {sid: store.station(sid).model_copy(update=update)}


def _amenity(store, amenity_id):
    return next(a for a in store.amenities if a.amenity_id == amenity_id)


def _cutoff(store):
    return stale_cutoff(store.station("FIX-ST-001").provenance.retrieved_at)


# ------------------------------------------------------------------ CQ10: restricted stations


def _cq10(g):
    return [(r.station, str(r.access)) for r in run_competency_query(g, "cq10")]


def test_cq10_restricted_station(graph):
    assert _cq10(graph) == [(station_uri("FIX-ST-008"), "restricted")]


def test_cq10_several_and_none(store):
    g = _rebuilt(store, stations=_station(store, "FIX-ST-001", access="restricted"))
    assert _cq10(g) == [(station_uri("FIX-ST-001"), "restricted"), (station_uri("FIX-ST-008"), "restricted")]
    assert _cq10(_rebuilt(store, stations=_station(store, "FIX-ST-008", access="public"))) == []


# ------------------------------------------------------------------ CQ11: stations not available


def test_cq11_unavailable_station(graph):
    rows = run_competency_query(graph, "cq11")
    assert [(r.station, str(r.status)) for r in rows] == [(station_uri("FIX-ST-007"), "temporarily_unavailable")]


def test_cq11_every_status_but_available_ordered_by_status(store):
    g = _rebuilt(store, stations={**_station(store, "FIX-ST-001", status=StationStatus.PLANNED),
                                  **_station(store, "FIX-ST-002", status=StationStatus.UNKNOWN)})
    assert [(r.station, str(r.status)) for r in run_competency_query(g, "cq11")] == [
        (station_uri("FIX-ST-001"), "planned"),
        (station_uri("FIX-ST-007"), "temporarily_unavailable"),
        (station_uri("FIX-ST-002"), "unknown"),  # unknown is reported, never read as available
    ]


# ------------------------------------------------------------------ CQ12: port counts


def _cq12(g, sid):
    from clickless.retrieval.kg.vocab import connector_name

    rows = run_competency_query(g, "cq12", station=station_uri(sid))
    return [(connector_name(r.connector), str(r.level), _py(r.points)) for r in rows]


def test_cq12_points_per_connector_and_level(graph):
    assert _cq12(graph, "FIX-ST-004") == [("CCS1", "dc_fast", 2), ("NACS", "dc_fast", 4)]
    assert _cq12(graph, "FIX-ST-002") == [("CCS1", "dc_fast", 2), ("CHADEMO", "dc_fast", 1)]
    assert _cq12(graph, "FIX-ST-005") == [("J1772", "l2", 2)]


def test_cq12_sums_entries_of_one_connector_and_level_only(store):
    st = store.station("FIX-ST-004")
    more_dc = st.ports[0].model_copy(update={"count": 3, "power_kw": 62.5})
    ac = st.ports[0].model_copy(update={"level": ChargerLevel.L2, "count": 1, "power_kw": 11.5})
    g = _rebuilt(store, stations=_station(store, "FIX-ST-004", ports=[*st.ports, more_dc, ac]))
    assert _cq12(g, "FIX-ST-004") == [("CCS1", "dc_fast", 5), ("CCS1", "l2", 1), ("NACS", "dc_fast", 4)]


# ------------------------------------------------------------------ CQ13: opening hours


def _cq13(g, sid):
    rows = run_competency_query(g, "cq13", station=station_uri(sid))
    return [(r.amenity, str(r.category), str(r.hours)) for r in rows]


def test_cq13_hours_and_unknown(graph):
    assert _cq13(graph, "FIX-ST-004") == [
        (amenity_uri("fix:a05"), "food", "Mo-Su 10:00-21:00"),
        (amenity_uri("fix:a06"), "restroom", "unknown"),  # untagged: unknown, never "always open"
    ]


def test_cq13_tagged_hours_are_reported_as_tagged(store):
    a06 = _amenity(store, "fix:a06").model_copy(update={"opening_hours": "24/7"})
    g = _rebuilt(store, amenities={"fix:a06": a06})
    assert _cq13(g, "FIX-ST-004")[1] == (amenity_uri("fix:a06"), "restroom", "24/7")


# ------------------------------------------------------------------ CQ14: power the vehicle gets at a station


def _cq14(g, vehicle, sid):
    rows = run_competency_query(g, "cq14", vehicle=vehicle_uri(vehicle), station=station_uri(sid))
    return [(r.port, _py(r.portKw), _py(r.vehicleKw), _py(r.effectiveKw)) for r in rows]


def _kw(*values):
    return [None if v is None else pytest.approx(v) for v in values]


def test_cq14_lower_of_port_and_vehicle(graph):
    rows = _cq14(graph, COMET, "FIX-ST-004")  # Comet: max 170 kW; CCS1 by adapter, NACS native
    assert [r[0] for r in rows] == [port_uri("FIX-ST-004", 0), port_uri("FIX-ST-004", 1)]
    assert [list(r[1:]) for r in rows] == [_kw(50, 170, 50), _kw(250, 170, 170)]
    assert [list(r[1:]) for r in _cq14(graph, SPARK, "FIX-ST-001")] == [_kw(150, 230, 150)]


def test_cq14_dc_fast_and_usable_ports_only(store, graph):
    assert _cq14(graph, LEAFY, "FIX-ST-005") == []  # J1772 at L2: not a DC fast port
    assert _cq14(graph, SPARK, "FIX-ST-003") == []  # NACS, no adapter
    spark = store.vehicles[SPARK].model_copy(update={"adapters": [Connector.NACS]})
    g = _rebuilt(store, vehicles={SPARK: spark})
    assert [list(r[1:]) for r in _cq14(g, SPARK, "FIX-ST-003")] == [_kw(250, 230, 230)]


def test_cq14_unknown_power_gives_unknown_effective_power(store):
    spark = store.vehicles[SPARK].model_copy(update={"max_dc_kw": None})
    g = _rebuilt(store, vehicles={SPARK: spark})
    assert [list(r[1:]) for r in _cq14(g, SPARK, "FIX-ST-001")] == [_kw(150, None, None)]
    st = store.station("FIX-ST-001")
    no_power = [st.ports[0].model_copy(update={"power_kw": None})]
    g = _rebuilt(store, stations=_station(store, "FIX-ST-001", ports=no_power))
    assert [list(r[1:]) for r in _cq14(g, SPARK, "FIX-ST-001")] == [_kw(None, 230, None)]


# ------------------------------------------------------------------ CQ15: freshness labels


def _expected_label(prov):
    """The label data/provenance.py gives the same record (status_label's note)."""
    if prov.status == DataStatus.STATIC:
        return "reference data"
    return {None: "source update date unknown", True: "may be out of date", False: "up to date"}[is_stale(prov)]


def test_cq15_static_fixtures_are_reference_data(store, graph):
    rows = run_competency_query(graph, "cq15", cutoff=_cutoff(store))
    assert {str(r.freshness) for r in rows} == {"reference data"} and len(rows) == len(store.stations)


def test_cq15_matches_provenance_labels(store):
    day = timedelta(days=1)
    changes = {  # (status, age): one per label, plus both sides of the stale boundary
        "FIX-ST-001": (DataStatus.CACHED, 366 * day),
        "FIX-ST-002": (DataStatus.CACHED, 366 * day - timedelta(seconds=1)),
        "FIX-ST-003": (DataStatus.CACHED, None),
        "FIX-ST-004": (DataStatus.STATIC, 900 * day),
        "FIX-ST-005": (DataStatus.RUNTIME, 30 * day),
    }
    stations = {}
    for sid, (status, age) in changes.items():
        prov = store.station(sid).provenance
        updated = None if age is None else prov.retrieved_at - age
        stations.update(_station(store, sid, provenance=prov.model_copy(update={"status": status,
                                                                              "source_updated_at": updated})))
    g = _rebuilt(store, stations=stations)

    rows = run_competency_query(g, "cq15", cutoff=_cutoff(store))

    got = {r.station: str(r.freshness) for r in rows}
    for sid in changes:
        prov = stations[sid].provenance
        assert got[station_uri(sid)] == _expected_label(prov), sid
    assert [got[station_uri(s)] for s in changes] == [
        "may be out of date", "up to date", "source update date unknown", "reference data", "up to date",
    ]
    assert "may be out of date" in status_label(stations["FIX-ST-001"].provenance)
    assert "source update date unknown" in status_label(stations["FIX-ST-003"].provenance)


# ------------------------------------------------------------------ CQ16: amenities of a category by distance


def _cq16(g, sid, category):
    rows = run_competency_query(g, "cq16", station=station_uri(sid), category=category)
    return [(r.amenity, float(r.distanceM)) for r in rows]


def _metres(station, amenity):
    return haversine_km(station.lat, station.lon, amenity.lat, amenity.lon) * 1000


def test_cq16_category_and_distance(store, graph):
    st3, st4 = store.station("FIX-ST-003"), store.station("FIX-ST-004")
    a03, a06 = _amenity(store, "fix:a03"), _amenity(store, "fix:a06")
    assert _cq16(graph, "FIX-ST-003", "food") == [(amenity_uri("fix:a03"), pytest.approx(_metres(st3, a03)))]
    assert _cq16(graph, "FIX-ST-004", "restroom") == [(amenity_uri("fix:a06"), pytest.approx(_metres(st4, a06)))]
    assert _cq16(graph, "FIX-ST-004", "coffee") == []


def test_cq16_nearest_first(store):
    st = store.station("FIX-ST-004")
    near = _amenity(store, "fix:a05")
    # ids sort the other way round from distance, so the order must come from ORDER BY ?distanceM
    farther = near.model_copy(update={"amenity_id": "fix:a00", "name": "Farther Diner",
                                      "lat": st.lat + (near.lat - st.lat) * 2, "lon": st.lon + (near.lon - st.lon) * 2})
    closer = near.model_copy(update={"amenity_id": "fix:a99", "name": "Closer Diner",
                                     "lat": st.lat + (near.lat - st.lat) / 2, "lon": st.lon + (near.lon - st.lon) / 2})
    assert _metres(st, farther) <= 400
    g = _rebuilt(store, extra_amenities=[farther, closer])
    rows = _cq16(g, "FIX-ST-004", "food")
    assert [r[0] for r in rows] == [amenity_uri("fix:a99"), amenity_uri("fix:a05"), amenity_uri("fix:a00")]
    assert [r[1] for r in rows] == sorted(r[1] for r in rows)


# ------------------------------------------------------------------ CQ17: incomplete provenance


def _cq17(g):
    rows = run_competency_query(g, "cq17")
    return [(r.station, str(r.sourceName), _py(r.missingRecordId), _py(r.missingUrl)) for r in rows]


def test_cq17_synthetic_records_have_neither(store, graph):
    assert _cq17(graph) == [(station_uri(s), "SYNTHETIC", True, True) for s in sorted(store.stations)]


def test_cq17_complete_records_are_not_listed(store):
    def prov(sid, **update):
        return store.station(sid).provenance.model_copy(update=update)

    g = _rebuilt(store, stations={
        **_station(store, "FIX-ST-001", provenance=prov("FIX-ST-001", source_record_id="1",
                                                        source_url="https://example.org/afdc/1")),
        **_station(store, "FIX-ST-002", provenance=prov("FIX-ST-002", source_record_id="2")),
        **_station(store, "FIX-ST-003", provenance=prov("FIX-ST-003", source_url="https://example.org/afdc/3")),
    })
    rows = {r[0]: r[2:] for r in _cq17(g)}
    assert station_uri("FIX-ST-001") not in rows
    assert rows[station_uri("FIX-ST-002")] == (False, True)
    assert rows[station_uri("FIX-ST-003")] == (True, False)


# ------------------------------------------------------------------ CQ18: DC fast stations per network


def _cq18(g):
    return [(str(r.networkName), _py(r.stations)) for r in run_competency_query(g, "cq18")]


def test_cq18_counts(graph):
    # FixtureLocal's only station (FIX-ST-005) is Level 2, so it has no DC fast station to count
    assert _cq18(graph) == [("FixtureNet", 5), ("FixtureFast", 1), ("FixtureFleet", 1)]


def test_cq18_counts_a_station_once_and_skips_unknown_networks(store):
    st4, st5 = store.station("FIX-ST-004"), store.station("FIX-ST-005")
    dc = st4.ports[0].model_copy(update={"count": 1})
    g = _rebuilt(store, stations={
        **_station(store, "FIX-ST-001", network=None),
        **_station(store, "FIX-ST-004", ports=[*st4.ports, dc]),  # a third DC port: still one station
        **_station(store, "FIX-ST-005", ports=[*st5.ports, dc]),
    })
    assert _cq18(g) == [("FixtureNet", 4), ("FixtureFast", 1), ("FixtureFleet", 1), ("FixtureLocal", 1)]


# ------------------------------------------------------------------ CQ19: no known power at all


def test_cq19_none_in_the_fixtures(graph):
    assert run_competency_query(graph, "cq19") == []


def test_cq19_only_stations_with_no_known_port_power(store):
    st2, st4 = store.station("FIX-ST-002"), store.station("FIX-ST-004")
    unknown = {"power_kw": None}
    g = _rebuilt(store, stations={
        **_station(store, "FIX-ST-002", ports=[p.model_copy(update=unknown) for p in st2.ports]),
        **_station(store, "FIX-ST-004", ports=[st4.ports[0].model_copy(update=unknown), st4.ports[1]]),
    })
    assert [r.station for r in run_competency_query(g, "cq19")] == [station_uri("FIX-ST-002")]


# ------------------------------------------------------------------ CQ20: food + accessible restroom


def _cq20(g):
    return [(r.station, r.food, r.restroom) for r in run_competency_query(g, "cq20")]


def test_cq20_food_and_accessible_restroom(graph):
    # FIX-ST-003 has food (fix:a03) and a restroom (fix:a04), but the restroom is untagged
    assert _cq20(graph) == [(station_uri("FIX-ST-004"), amenity_uri("fix:a05"), amenity_uri("fix:a06"))]


def test_cq20_only_an_explicit_yes_counts(store):
    a04 = _amenity(store, "fix:a04").model_copy(update={"wheelchair": "yes"})
    a06 = _amenity(store, "fix:a06").model_copy(update={"wheelchair": "limited"})
    g = _rebuilt(store, amenities={"fix:a04": a04, "fix:a06": a06})
    assert _cq20(g) == [(station_uri("FIX-ST-003"), amenity_uri("fix:a03"), amenity_uri("fix:a04"))]
