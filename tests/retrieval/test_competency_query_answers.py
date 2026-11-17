"""Expected answers of CQ2, CQ4, CQ5 and CQ6 on the fixture KG (Aarya's learning swap, TEAM_PLAN §2).

Each query file is run unchanged except for its `VALUES ?x { cc:x_REPLACE_ME }` placeholder, which is
removed and bound with initBindings, as in the CQ3 test in test_kg_enrichment.py. The expected rows
follow from data/fixtures (stations FIX-ST-001..008, amenities fix:a01..a08, vehicles Spark/Comet/Leafy).
"""

import re
from datetime import datetime, timezone

import pytest

from clickless.config import repo_path
from clickless.data import DataStore
from clickless.retrieval.kg.build import amenity_uri, build_graph, port_uri, station_uri, vehicle_uri

pytest.importorskip("rdflib")  # CI installs the kg extra; this only skips on a machine without it

CQ_DIR = "src/clickless/retrieval/kg/competency_queries"
SPARK, COMET, LEAFY = "fixture-spark-lr-2025", "fixture-comet-awd-2026", "fixture-leafy-2020"


def _run(graph, file_name: str, **bindings):
    """Run a CQ file; each keyword binds the variable whose VALUES placeholder is removed."""
    from rdflib.plugins.sparql import prepareQuery

    text = repo_path(f"{CQ_DIR}/{file_name}").read_text(encoding="utf-8")
    for var in bindings:
        text, n = re.subn(rf"VALUES \?{var} \{{ cc:{var}_REPLACE_ME \}}", "", text)
        assert n == 1, f"{file_name}: no placeholder for ?{var}"
    return list(graph.query(prepareQuery(text), initBindings=bindings))


def _name(node) -> str:
    return str(node).rsplit("#", 1)[-1]


@pytest.fixture(scope="module")
def graph(store):
    return build_graph(store)


# ------------------------------------------------------------------ CQ2: compatible stations + food


def test_cq2_comet_uses_native_nacs_and_its_ccs1_adapter(graph):
    rows = _run(graph, "cq02_compatible_stations_with_food.rq", vehicle=vehicle_uri(COMET))
    assert {(r.station, r.amenity, _name(r.connector)) for r in rows} == {
        (station_uri("FIX-ST-002"), amenity_uri("fix:a02"), "CCS1"),  # via the adapter
        (station_uri("FIX-ST-003"), amenity_uri("fix:a03"), "NACS"),
        (station_uri("FIX-ST-004"), amenity_uri("fix:a05"), "CCS1"),
        (station_uri("FIX-ST-004"), amenity_uri("fix:a05"), "NACS"),
        (station_uri("FIX-ST-006"), amenity_uri("fix:a08"), "CCS1"),
    }


def test_cq2_spark_gets_only_ccs1_stations_with_food(graph):
    rows = _run(graph, "cq02_compatible_stations_with_food.rq", vehicle=vehicle_uri(SPARK))
    assert {(r.station, r.amenity, _name(r.connector)) for r in rows} == {
        (station_uri("FIX-ST-002"), amenity_uri("fix:a02"), "CCS1"),
        (station_uri("FIX-ST-004"), amenity_uri("fix:a05"), "CCS1"),
        (station_uri("FIX-ST-006"), amenity_uri("fix:a08"), "CCS1"),
    }
    # FIX-ST-001 is CCS1 but its only nearby amenity (fix:a01) is coffee, not food
    assert station_uri("FIX-ST-001") not in {r.station for r in rows}


def test_cq2_leafy_gets_the_chademo_station(graph):
    rows = _run(graph, "cq02_compatible_stations_with_food.rq", vehicle=vehicle_uri(LEAFY))
    # FIX-ST-005 has the Leafy's J1772 plug, but its nearby amenity (fix:a07) is grocery, not food
    assert [(r.station, r.amenity, _name(r.connector)) for r in rows] == [
        (station_uri("FIX-ST-002"), amenity_uri("fix:a02"), "CHADEMO")
    ]


# ------------------------------------------------------------------ CQ4: DC power and its provenance


def test_cq4_dc_ports_with_power_and_source(graph, store):
    rows = _run(graph, "cq04_dc_power_provenance.rq", station=station_uri("FIX-ST-004"))
    retrieved = store.station("FIX-ST-004").provenance.retrieved_at
    assert [
        (r.port, float(r.power.toPython()), str(r.sourceName), r.retrievedAt.toPython(), r.sourceUpdatedAt)
        for r in rows
    ] == [
        (port_uri("FIX-ST-004", 1), 250.0, "SYNTHETIC", retrieved, None),  # NACS, highest power first
        (port_uri("FIX-ST-004", 0), 50.0, "SYNTHETIC", retrieved, None),  # CCS1
    ]


def test_cq4_returns_nothing_for_a_level2_only_station(graph):
    assert _run(graph, "cq04_dc_power_provenance.rq", station=station_uri("FIX-ST-005")) == []


# ------------------------------------------------------------------ CQ5: adapter-only connectors


@pytest.mark.parametrize("vehicle, expected", [(COMET, ["CCS1"]), (SPARK, []), (LEAFY, [])])
def test_cq5_adapter_only_connectors(graph, vehicle, expected):
    rows = _run(graph, "cq05_adapter_only_connectors.rq", vehicle=vehicle_uri(vehicle))
    assert [_name(r.connector) for r in rows] == expected


# ------------------------------------------------------------------ CQ6: stale station records


def test_cq6_reports_stations_updated_before_the_cutoff(store):
    """The fixtures carry no source update dates, so two stations get one here: FIX-ST-001 stale
    (before the query's 2025-11-03 cutoff), FIX-ST-002 recent. Stations without a date are not
    returned: their age cannot be judged."""
    stale, recent = datetime(2024, 1, 15, tzinfo=timezone.utc), datetime(2026, 6, 1, tzinfo=timezone.utc)
    dated = {"FIX-ST-001": stale, "FIX-ST-002": recent}
    stations = [
        st.model_copy(update={"provenance": st.provenance.model_copy(update={"source_updated_at": dated[sid]})})
        if sid in dated
        else st
        for sid, st in store.stations.items()
    ]
    g = build_graph(DataStore(stations, list(store.vehicles.values()), store.amenities, store.places,
                              store.routes_dir))

    rows = _run(g, "cq06_stale_station_records.rq")

    assert [(r.station, r.sourceUpdatedAt.toPython()) for r in rows] == [(station_uri("FIX-ST-001"), stale)]


def test_cq6_returns_nothing_without_source_update_dates(graph):
    assert _run(graph, "cq06_stale_station_records.rq") == []
