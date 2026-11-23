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


# ================================================================== CQ1, CQ7-CQ9 (#157, on Manas's behalf)

from clickless.contracts import ChargerLevel, Claim, Evidence, PlanResult  # noqa: E402
from clickless.retrieval.kg.build import add_explanation, claim_uri, explanation_uri  # noqa: E402

# ------------------------------------------------------------------ CQ1: compatible ports and power


def _cq1(graph, vehicle):
    rows = run_competency_query(graph, "cq01", vehicle=vehicle_uri(vehicle))
    return [(r.station, r.port, connector_name(r.connector), _power(r)) for r in rows]


def _p(station_id, index, connector, power):
    return (station_uri(station_id), port_uri(station_id, index), connector, power)


SPARK_CQ1 = [  # CCS1 native: every CCS1 port, fastest first, then station and port order
    _p("FIX-ST-001", 0, "CCS1", 150.0),
    _p("FIX-ST-006", 0, "CCS1", 150.0),
    _p("FIX-ST-007", 0, "CCS1", 150.0),
    _p("FIX-ST-008", 0, "CCS1", 150.0),
    _p("FIX-ST-002", 0, "CCS1", 50.0),
    _p("FIX-ST-004", 0, "CCS1", 50.0),
]


def test_cq1_comet_native_nacs_and_adapted_ccs1(graph):
    assert _cq1(graph, COMET) == [
        _p("FIX-ST-003", 0, "NACS", 250.0),
        _p("FIX-ST-004", 1, "NACS", 250.0),
        *SPARK_CQ1[:4],  # the CCS1 adapter, at DC fast ports
        *SPARK_CQ1[4:],
    ]


def test_cq1_spark_and_leafy(graph):
    assert _cq1(graph, SPARK) == SPARK_CQ1
    assert _cq1(graph, LEAFY) == [_p("FIX-ST-002", 1, "CHADEMO", 50.0), _p("FIX-ST-005", 0, "J1772", 7.2)]


def test_cq1_an_adapter_counts_at_dc_fast_ports_only(store):
    """planning/filters.py::compatible_ports: an adapter is never inferred for an AC port (ADR 0009)."""
    spark = store.vehicles[SPARK]
    st5 = store.station("FIX-ST-005")
    nacs_ac = st5.ports[0].model_copy(update={"connector": Connector.NACS, "power_kw": 11.5})
    g = _rebuilt(
        store,
        vehicles={SPARK: spark.model_copy(update={"adapters": [Connector.NACS]})},
        stations={"FIX-ST-005": st5.model_copy(update={"ports": [*st5.ports, nacs_ac]})},
    )
    rows = _cq1(g, SPARK)
    assert rows[:2] == [_p("FIX-ST-003", 0, "NACS", 250.0), _p("FIX-ST-004", 1, "NACS", 250.0)]  # DC: yes
    assert _p("FIX-ST-005", 1, "NACS", 11.5) not in rows  # AC: no
    assert nacs_ac.level == ChargerLevel.L2


def test_cq1_lists_a_port_once_when_its_connector_is_native_and_adapted(store):
    spark = store.vehicles[SPARK]
    g = _rebuilt(store, vehicles={SPARK: spark.model_copy(update={"adapters": [Connector.CCS1]})})
    assert _cq1(g, SPARK) == SPARK_CQ1


def test_cq1_unknown_power_port_is_listed_last_with_no_power(store):
    st = store.station("FIX-ST-004")
    unknown = st.ports[0].model_copy(update={"power_kw": None})
    g = _rebuilt(store, stations={"FIX-ST-004": st.model_copy(update={"ports": [*st.ports, unknown]})})
    assert _cq1(g, SPARK) == [*SPARK_CQ1, _p("FIX-ST-004", 2, "CCS1", None)]


@pytest.mark.parametrize("level, expected", [(ChargerLevel.DC_FAST, True), (ChargerLevel.L2, False)])
def test_cq2_an_adapter_counts_at_dc_fast_ports_only(store, level, expected):
    """FIX-ST-003 is near food (fix:a03); with only a NACS port at `level`, Spark's NACS adapter
    reaches it at DC fast and not at L2 (same rule as CQ1)."""
    spark = store.vehicles[SPARK]
    st3 = store.station("FIX-ST-003")
    port = st3.ports[0].model_copy(update={"level": level, "power_kw": 250.0 if expected else 11.5})
    g = _rebuilt(
        store,
        vehicles={SPARK: spark.model_copy(update={"adapters": [Connector.NACS]})},
        stations={"FIX-ST-003": st3.model_copy(update={"ports": [port]})},
    )
    stations = {r[0] for r in _cq2(g, SPARK)}
    assert (station_uri("FIX-ST-003") in stations) is expected


# ------------------------------------------------------------------ CQ7: network of a station


def _cq7(graph, station_id):
    return [(str(r.network).rsplit("#", 1)[-1], str(r.networkName))
            for r in run_competency_query(graph, "cq07", station=station_uri(station_id))]


@pytest.mark.parametrize(
    "station_id, network",
    [("FIX-ST-001", "FixtureNet"), ("FIX-ST-003", "FixtureFast"), ("FIX-ST-005", "FixtureLocal"),
     ("FIX-ST-008", "FixtureFleet")],
)
def test_cq7_network_of_a_station(graph, station_id, network):
    assert _cq7(graph, station_id) == [(f"network_{network}", network)]


def test_cq7_stations_of_one_network_share_its_node(graph):
    assert _cq7(graph, "FIX-ST-001") == _cq7(graph, "FIX-ST-002") == _cq7(graph, "FIX-ST-006")


def test_cq7_unknown_network_gives_no_row(store):
    st = store.station("FIX-ST-001")
    g = _rebuilt(store, stations={"FIX-ST-001": st.model_copy(update={"network": None})})
    assert _cq7(g, "FIX-ST-001") == []
    assert _cq7(g, "FIX-ST-002") == [("network_FixtureNet", "FixtureNet")]


# ------------------------------------------------------------------ CQ8: at least P kW for connector C


def _cq8(graph, connector, min_kw):
    from clickless.retrieval.kg import vocab

    rows = run_competency_query(graph, "cq08", connector=vocab.connector(connector), minKw=min_kw)
    return [(r.station, r.port, _power(r)) for r in rows]


CCS1_150 = [(station_uri(s), port_uri(s, 0), 150.0) for s in ("FIX-ST-001", "FIX-ST-006", "FIX-ST-007", "FIX-ST-008")]


def test_cq8_threshold_is_inclusive(graph):
    assert _cq8(graph, "CCS1", 150) == CCS1_150
    assert _cq8(graph, "CCS1", 100) == CCS1_150
    assert _cq8(graph, "CCS1", 151) == []


def test_cq8_lower_threshold_adds_slower_ports_in_power_order(graph):
    assert _cq8(graph, "CCS1", 50) == [
        *CCS1_150,
        (station_uri("FIX-ST-002"), port_uri("FIX-ST-002", 0), 50.0),
        (station_uri("FIX-ST-004"), port_uri("FIX-ST-004", 0), 50.0),
    ]


def test_cq8_only_the_bound_connector(graph):
    """FIX-ST-002 has CCS1 50 kW and CHAdeMO 50 kW: only the CHAdeMO port answers for CHADEMO."""
    assert _cq8(graph, "CHADEMO", 50) == [(station_uri("FIX-ST-002"), port_uri("FIX-ST-002", 1), 50.0)]
    assert _cq8(graph, "NACS", 250) == [
        (station_uri("FIX-ST-003"), port_uri("FIX-ST-003", 0), 250.0),
        (station_uri("FIX-ST-004"), port_uri("FIX-ST-004", 1), 250.0),
    ]


def test_cq8_decimal_threshold_meets_an_equal_port(graph):
    """7.2 kW meets "at least 7.2": the loader binds floats as exact decimals, as the graph stores them."""
    assert _cq8(graph, "J1772", 7.2) == [(station_uri("FIX-ST-005"), port_uri("FIX-ST-005", 0), 7.2)]
    assert _cq8(graph, "J1772", 7.21) == []


def test_cq8_unknown_power_never_meets_a_threshold(store):
    st = store.station("FIX-ST-002")
    unknown = st.ports[0].model_copy(update={"power_kw": None})
    g = _rebuilt(store, stations={"FIX-ST-002": st.model_copy(update={"ports": [unknown, st.ports[1]]})})
    assert station_uri("FIX-ST-002") not in {row[0] for row in _cq8(g, "CCS1", 0)}


# ------------------------------------------------------------------ CQ9: claims and their sources


def _evidence(store, subject_id, field, value):
    record = store.station(subject_id) if subject_id.startswith("FIX-ST") else next(
        a for a in store.amenities if a.amenity_id == subject_id)
    return Evidence(evidence_id=f"kg:{subject_id}:{field}", subject_id=subject_id, field=field, value=value,
                    provenance=record.provenance)


def _cq9(graph, request_id):
    rows = run_competency_query(graph, "cq09", explanation=explanation_uri(request_id))
    return [(r.claim, str(r.text), None if r.source is None else str(r.source).rsplit("#", 1)[-1],
             None if r.sourceName is None else str(r.sourceName)) for r in rows]


def test_cq9_claims_with_their_sources(store):
    st = store.station("FIX-ST-004")
    distance = Evidence(evidence_id="kg:FIX-ST-004:near[fix:a06].distance_m", subject_id="FIX-ST-004",
                        field="near[fix:a06].distance_m", value=120.0,
                        provenance=st.provenance.model_copy(update={"source": "DERIVED"}))
    result = PlanResult(
        request_id="cq9", config="C", status="ok",
        evidence=[
            _evidence(store, "FIX-ST-004", "name", st.name),
            _evidence(store, "FIX-ST-004", "ports[0].power_kw", 50.0),
            _evidence(store, "fix:a06", "wheelchair", "yes"),
            distance,
        ],
        claims=[
            Claim(text="Stop at the hub (CCS1, 50 kW).",
                  supported_by=["verified:cq9:stop:0", "kg:FIX-ST-004:name", "kg:FIX-ST-004:ports[0].power_kw"]),
            Claim(text="Accessible restroom 120 m away.",
                  supported_by=["kg:fix:a06:wheelchair", "kg:FIX-ST-004:near[fix:a06].distance_m"]),
            Claim(text="Arrive with 22%.", supported_by=["verified:cq9:arrival"]),
            Claim(text="Cites a fact this result does not carry.", supported_by=["kg:FIX-ST-001:name"]),
        ],
    )
    g = build_graph(store)
    add_explanation(g, result)

    assert _cq9(g, "cq9") == [
        (claim_uri("cq9", 0), "Stop at the hub (CCS1, 50 kW).", "source_station_FIX-ST-004", "SYNTHETIC"),
        (claim_uri("cq9", 1), "Accessible restroom 120 m away.", "source_amenity_fix%3Aa06", "SYNTHETIC"),
        (claim_uri("cq9", 1), "Accessible restroom 120 m away.", "source_near_FIX-ST-004%7Cfix%3Aa06", "DERIVED"),
        (claim_uri("cq9", 2), "Arrive with 22%.", None, None),  # planning numbers: no KG source
        (claim_uri("cq9", 3), "Cites a fact this result does not carry.", None, None),  # ADR 0015: unsupported
    ]
    # two facts from one record give one source; the other explanation in the graph is not mixed in
    add_explanation(g, result.model_copy(update={"request_id": "other"}))
    assert len(_cq9(g, "cq9")) == 5


def test_cq9_on_a_real_config_c_result(store, demo_text):
    from clickless.config import load_config
    from clickless.pipeline import build_pipeline
    from clickless.retrieval.tfidf_matcher import TfidfMatcher

    pipe = build_pipeline(load_config("C"), store)
    pipe.matcher = TfidfMatcher(store)  # Sentence-BERT needs the nlp extra, which CI does not install
    result = pipe.run(demo_text, request_id="kg-c9")
    assert result.status == "ok" and result.claims
    g = build_graph(store)
    add_explanation(g, result)

    rows = _cq9(g, "kg-c9")
    assert [r[1] for r in rows if r[0] == claim_uri("kg-c9", 0)][0] == result.claims[0].text

    def expected_source(e):  # independent of build._evidence_source
        from urllib.parse import quote

        if e.field.startswith("near["):
            return "source_near_" + quote(f"{e.subject_id}|{e.field[5:e.field.index(']')]}", safe="")
        kind = "station" if e.subject_id in store.stations else "amenity"
        return f"source_{kind}_" + quote(e.subject_id, safe="")

    by_id = {e.evidence_id: e for e in result.evidence}
    for i, claim in enumerate(result.claims):
        got = sorted(r[2] for r in rows if r[0] == claim_uri("kg-c9", i) and r[2])
        want = sorted({expected_source(by_id[x]) for x in claim.supported_by if x in by_id})
        assert got == want, claim.text
    assert {r[0] for r in rows} == {claim_uri("kg-c9", i) for i in range(len(result.claims))}
    stop = result.alternatives[0].itinerary.stops[0]
    assert any(r[2] == f"source_station_{stop.station_id}" for r in rows)


@pytest.mark.parametrize("name, binding", [
    ("cq01", {"vehicle": vehicle_uri(SPARK)}),
    ("cq02", {"vehicle": vehicle_uri(COMET)}),
    ("cq04", {"station": station_uri("FIX-ST-004")}),
])
def test_row_order_comes_from_order_by_not_from_how_the_graph_was_loaded(graph, name, binding):
    """Ties (e.g. four 150 kW CCS1 ports) must be ordered by the query: the same triples loaded in
    reverse give the same rows."""
    from rdflib import Graph

    reversed_graph = Graph()
    for triple in reversed(list(graph)):
        reversed_graph.add(triple)
    assert run_competency_query(reversed_graph, name, **binding) == run_competency_query(graph, name, **binding)
