"""Expected answers of CQ2-CQ6 on the fixture KG (Aarya's learning swap, TEAM_PLAN §2).

Each query file runs unchanged through clickless.retrieval.kg.competency, which removes its
`VALUES ?x { cc:x_REPLACE_ME }` placeholders and binds them instead. The expected rows follow from
data/fixtures (stations FIX-ST-001..008, amenities fix:a01..a08, vehicles Spark/Comet/Leafy).
Where the fixtures lack a case a query must handle (a native connector that also has an adapter,
an unknown-power DC port, dated or "no" records), a test copies the records, changes them and
rebuilds the graph, so the case also goes through the KG builder's conventions.
"""

from datetime import timedelta

import pytest

from clickless.contracts import Connector, DataStatus
from clickless.data import DataStore
from clickless.data.provenance import STALE_AFTER_DAYS, is_stale, stale_cutoff
from clickless.retrieval.kg.build import amenity_uri, build_graph, port_uri, station_uri, vehicle_uri
from clickless.retrieval.kg.competency import competency_query_text, run_competency_query

pytest.importorskip("rdflib")  # CI installs the kg extra; this only skips on a machine without it

from clickless.retrieval.kg.vocab import connector_name  # noqa: E402  (imports rdflib)

SPARK, COMET, LEAFY = "fixture-spark-lr-2025", "fixture-comet-awd-2026", "fixture-leafy-2020"


@pytest.fixture(scope="module")
def graph(store):
    return build_graph(store)


def _rebuilt(store, stations=None, vehicles=None, amenities=None):
    """Graph of the fixture store with some records replaced (dicts keyed by id)."""
    return build_graph(
        DataStore(
            [(stations or {}).get(sid, st) for sid, st in store.stations.items()],
            [(vehicles or {}).get(vid, v) for vid, v in store.vehicles.items()],
            [(amenities or {}).get(a.amenity_id, a) for a in store.amenities],
            store.places,
            store.routes_dir,
        )
    )


def _power(row) -> float | None:
    return None if row.power is None else float(row.power.toPython())


# ------------------------------------------------------------------ the shared loader


def test_every_placeholder_must_be_bound(graph):
    with pytest.raises(ValueError, match=r"unbound placeholder.*'vehicle'"):
        run_competency_query(graph, "cq02")
    with pytest.raises(ValueError, match=r"unbound placeholder.*'cutoff'"):
        run_competency_query(graph, "cq06")
    with pytest.raises(ValueError, match=r"no VALUES placeholder for \?station"):
        run_competency_query(graph, "cq02", vehicle=vehicle_uri(SPARK), station=station_uri("FIX-ST-001"))


def test_bound_text_has_no_placeholder_left():
    assert "REPLACE_ME" not in competency_query_text("cq06_stale_station_records.rq", "cutoff")


# ------------------------------------------------------------------ CQ2: compatible stations + food


def _cq2(graph, vehicle):
    rows = run_competency_query(graph, "cq02", vehicle=vehicle_uri(vehicle))
    return [(r.station, r.amenity, connector_name(r.connector)) for r in rows]


def test_cq2_comet_uses_native_nacs_and_its_ccs1_adapter(graph):
    assert _cq2(graph, COMET) == [
        (station_uri("FIX-ST-002"), amenity_uri("fix:a02"), "CCS1"),  # via the adapter
        (station_uri("FIX-ST-003"), amenity_uri("fix:a03"), "NACS"),
        (station_uri("FIX-ST-004"), amenity_uri("fix:a05"), "CCS1"),
        (station_uri("FIX-ST-004"), amenity_uri("fix:a05"), "NACS"),
        (station_uri("FIX-ST-006"), amenity_uri("fix:a08"), "CCS1"),
    ]


SPARK_CQ2 = [
    (station_uri("FIX-ST-002"), amenity_uri("fix:a02"), "CCS1"),
    (station_uri("FIX-ST-004"), amenity_uri("fix:a05"), "CCS1"),
    (station_uri("FIX-ST-006"), amenity_uri("fix:a08"), "CCS1"),
]


def test_cq2_spark_gets_only_ccs1_stations_with_food(graph):
    # FIX-ST-001 is CCS1 but its only nearby amenity (fix:a01) is coffee, not food
    assert _cq2(graph, SPARK) == SPARK_CQ2


def test_cq2_lists_a_station_once_when_the_connector_is_both_native_and_adapted(store):
    """A CCS1 adapter on a CCS1-native car matches each port twice; DISTINCT keeps one row."""
    spark = store.vehicles[SPARK]
    g = _rebuilt(store, vehicles={SPARK: spark.model_copy(update={"adapters": [Connector.CCS1]})})
    assert _cq2(g, SPARK) == SPARK_CQ2


def test_cq2_leafy_gets_the_chademo_station(graph):
    # FIX-ST-005 has the Leafy's J1772 plug, but its nearby amenity (fix:a07) is grocery, not food
    assert _cq2(graph, LEAFY) == [(station_uri("FIX-ST-002"), amenity_uri("fix:a02"), "CHADEMO")]


# ------------------------------------------------------------------ CQ3: wheelchair status near a station


def test_cq3_unknown_is_reported_as_unknown(graph):
    rows = run_competency_query(graph, "cq03", station=station_uri("FIX-ST-002"))
    assert [(r.amenity, str(r.wheelchair)) for r in rows] == [(amenity_uri("fix:a02"), "unknown")]


def test_cq3_two_unknown_amenities(graph):
    rows = run_competency_query(graph, "cq03", station=station_uri("FIX-ST-003"))
    assert [(r.amenity, str(r.wheelchair)) for r in rows] == [
        (amenity_uri("fix:a03"), "unknown"),
        (amenity_uri("fix:a04"), "unknown"),
    ]


def test_cq3_limited_is_not_returned(graph):
    # fix:a07 near FIX-ST-005 is tagged "limited"
    assert run_competency_query(graph, "cq03", station=station_uri("FIX-ST-005")) == []


@pytest.mark.parametrize(
    "a06, expected",
    [
        ("yes", [("fix:a05", "yes"), ("fix:a06", "yes")]),
        ("unknown", [("fix:a05", "yes"), ("fix:a06", "unknown")]),  # "yes" first
        ("limited", [("fix:a05", "yes")]),
        ("no", [("fix:a05", "yes")]),
    ],
)
def test_cq3_returns_yes_and_unknown_but_not_limited_or_no(store, a06, expected):
    """FIX-ST-004 has fix:a05 ("yes") and fix:a06, whose tag is varied here."""
    amenity = next(a for a in store.amenities if a.amenity_id == "fix:a06")
    g = _rebuilt(store, amenities={"fix:a06": amenity.model_copy(update={"wheelchair": a06})})
    rows = run_competency_query(g, "cq03", station=station_uri("FIX-ST-004"))
    assert [(r.amenity, str(r.wheelchair)) for r in rows] == [(amenity_uri(a), w) for a, w in expected]


# ------------------------------------------------------------------ CQ4: DC power and its provenance


def test_cq4_dc_ports_with_power_and_source(graph, store):
    rows = run_competency_query(graph, "cq04", station=station_uri("FIX-ST-004"))
    retrieved = store.station("FIX-ST-004").provenance.retrieved_at
    assert [(r.port, _power(r), str(r.sourceName), r.retrievedAt.toPython(), r.sourceUpdatedAt) for r in rows] == [
        (port_uri("FIX-ST-004", 1), 250.0, "SYNTHETIC", retrieved, None),  # NACS, highest power first
        (port_uri("FIX-ST-004", 0), 50.0, "SYNTHETIC", retrieved, None),  # CCS1
    ]


def test_cq4_unknown_power_port_is_returned_with_empty_power(store):
    """ADR 0010: a DC port whose power the source doesn't report is listed, never dropped or guessed."""
    st = store.station("FIX-ST-004")
    unknown = st.ports[0].model_copy(update={"power_kw": None})
    g = _rebuilt(store, stations={"FIX-ST-004": st.model_copy(update={"ports": [*st.ports, unknown]})})

    rows = run_competency_query(g, "cq04", station=station_uri("FIX-ST-004"))

    assert [(r.port, _power(r)) for r in rows] == [
        (port_uri("FIX-ST-004", 1), 250.0),
        (port_uri("FIX-ST-004", 0), 50.0),
        (port_uri("FIX-ST-004", 2), None),  # unbound sorts last under DESC
    ]
    assert rows[-1].power is None and str(rows[-1].sourceName) == "SYNTHETIC"


def test_cq4_returns_nothing_for_a_level2_only_station(graph):
    assert run_competency_query(graph, "cq04", station=station_uri("FIX-ST-005")) == []


# ------------------------------------------------------------------ CQ5: adapter-only connectors


@pytest.mark.parametrize("vehicle, expected", [(COMET, ["CCS1"]), (SPARK, []), (LEAFY, [])])
def test_cq5_adapter_only_connectors(graph, vehicle, expected):
    rows = run_competency_query(graph, "cq05", vehicle=vehicle_uri(vehicle))
    assert [connector_name(r.connector) for r in rows] == expected


def test_cq5_a_connector_with_both_native_support_and_an_adapter_is_not_adapter_only(store):
    """Leafy: J1772 native and adapted (FILTER NOT EXISTS drops it), NACS adapted only."""
    leafy = store.vehicles[LEAFY]
    g = _rebuilt(store, vehicles={LEAFY: leafy.model_copy(update={"adapters": [Connector.J1772, Connector.NACS]})})
    rows = run_competency_query(g, "cq05", vehicle=vehicle_uri(LEAFY))
    assert [connector_name(r.connector) for r in rows] == ["NACS"]


# ------------------------------------------------------------------ CQ6: stale station records


def test_stale_cutoff_is_the_is_stale_boundary(store):
    prov = store.station("FIX-ST-001").provenance.model_copy(update={"status": DataStatus.CACHED})
    cutoff = stale_cutoff(prov.retrieved_at)
    assert is_stale(prov.model_copy(update={"source_updated_at": cutoff})) is True
    assert is_stale(prov.model_copy(update={"source_updated_at": cutoff + timedelta(seconds=1)})) is False


def _dated(store, updates):
    """Station copies with the given status and source update time, keyed by station id."""
    out = {}
    for sid, (status, age) in updates.items():
        prov = store.station(sid).provenance
        prov = prov.model_copy(update={"status": status, "source_updated_at": prov.retrieved_at - age})
        out[sid] = store.station(sid).model_copy(update={"provenance": prov})
    return out


def test_cq6_matches_is_stale_at_the_boundary(store):
    """Fixture records are static and undated, so some get a status and an age here."""
    one_day, n = timedelta(days=1), STALE_AFTER_DAYS
    dated = _dated(
        store,
        {
            "FIX-ST-001": (DataStatus.CACHED, (n + 1) * one_day),  # .days == 366: stale, just inside
            "FIX-ST-002": (DataStatus.CACHED, (n + 1) * one_day - timedelta(seconds=1)),  # .days == 365: not stale
            "FIX-ST-003": (DataStatus.CACHED, n * one_day),  # exactly 365 days: not stale
            "FIX-ST-004": (DataStatus.CACHED, 3 * n * one_day),  # clearly stale
            "FIX-ST-005": (DataStatus.STATIC, 3 * n * one_day),  # static: cannot judge (is_stale None)
            "FIX-ST-006": (DataStatus.CACHED, 30 * one_day),  # recent
        },
    )
    retrieved = store.station("FIX-ST-001").provenance.retrieved_at
    assert {st.provenance.retrieved_at for st in store.stations.values()} == {retrieved}  # one ?cutoff fits all
    g = _rebuilt(store, stations=dated)

    rows = run_competency_query(g, "cq06", cutoff=stale_cutoff(retrieved))

    stale = [
        (station_uri(sid), st.provenance.source_updated_at, retrieved)
        for sid, st in sorted(dated.items(), key=lambda kv: (kv[1].provenance.source_updated_at, kv[0]))
        if is_stale(st.provenance)
    ]
    assert stale == [  # what is_stale says, oldest first ...
        (station_uri("FIX-ST-004"), retrieved - 3 * n * one_day, retrieved),
        (station_uri("FIX-ST-001"), retrieved - (n + 1) * one_day, retrieved),
    ]
    # ... is exactly what CQ6 returns, retrieval time included
    assert [(r.station, r.sourceUpdatedAt.toPython(), r.retrievedAt.toPython()) for r in rows] == stale


def test_cq6_returns_nothing_without_source_update_dates(graph, store):
    retrieved = store.station("FIX-ST-001").provenance.retrieved_at
    assert run_competency_query(graph, "cq06", cutoff=stale_cutoff(retrieved)) == []
