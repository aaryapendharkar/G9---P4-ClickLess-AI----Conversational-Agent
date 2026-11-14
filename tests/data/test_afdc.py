"""AFDC station normalization (ADR 0010). Records follow the All Stations API JSON fields."""

import json
from pathlib import Path

import pytest

from clickless.contracts import ChargerLevel, Connector
from clickless.data import DataStore, afdc
from clickless.data.afdc import AfdcSchemaError, stations_from_raw, stations_from_records, to_station
from clickless.data.validate import blocking_problems, validate_store

RETRIEVED = "2026-11-03T18:00:00+00:00"


def rec(**over):
    base = {
        "id": 1001,
        "station_name": "Electrify America - Walmart Flagstaff",
        "latitude": 35.19,
        "longitude": -111.62,
        "access_code": "public",
        "status_code": "E",
        "ev_network": "Electrify America",
        "ev_connector_types": ["J1772COMBO", "CHADEMO"],
        "ev_dc_fast_num": 4,
        "ev_level2_evse_num": None,
        "ev_level1_evse_num": None,
        "updated_at": "2023-09-14T14:01:49Z",
        "ev_charging_units": [
            {
                "network": "Electrify America",
                "charging_level": "dc_fast",
                "port_count": 1,
                "connectors": {"J1772COMBO": {"power_kw": 350, "port_count": 1}},
                "funding_sources": ["NEVI"],
            },
            {
                "network": "Electrify America",
                "charging_level": "dc_fast",
                "port_count": 1,
                "connectors": {"J1772COMBO": {"power_kw": 350, "port_count": 1}},
            },
            {
                "network": "Electrify America",
                "charging_level": "dc_fast",
                "port_count": 2,
                "connectors": {
                    "J1772COMBO": {"power_kw": 150, "port_count": 1},
                    "CHADEMO": {"power_kw": 50, "port_count": 1},
                },
            },
        ],
    }
    base.update(over)
    return base


def ports_of(st):
    return [(p.connector, p.level, p.power_kw, p.count) for p in st.ports]


def test_units_give_power_per_connector_and_merge_counts():
    st = to_station(rec(), RETRIEVED)
    assert st.station_id == "afdc:1001" and st.status.value == "available" and st.access == "public"
    assert sorted(ports_of(st), key=str) == sorted(
        [
            (Connector.CCS1, ChargerLevel.DC_FAST, 350.0, 2),
            (Connector.CCS1, ChargerLevel.DC_FAST, 150.0, 1),
            (Connector.CHADEMO, ChargerLevel.DC_FAST, 50.0, 1),
        ],
        key=str,
    )


def test_provenance_is_cached_afdc_with_update_date():
    p = to_station(rec(), RETRIEVED).provenance
    assert p.source == "AFDC" and p.status.value == "cached" and p.source_record_id == "1001"
    assert p.source_updated_at.isoformat() == "2023-09-14T14:01:49+00:00"
    assert p.retrieved_at.isoformat() == RETRIEVED
    assert "api_key" not in (p.source_url or "")


def test_tesla_supercharger_units():
    r = rec(
        id=2,
        ev_network="Tesla",
        ev_connector_types=["TESLA"],
        ev_charging_units=[{"charging_level": "dc_fast", "connectors": {"TESLA": {"power_kw": 250, "port_count": 8}}}],
    )
    assert ports_of(to_station(r, RETRIEVED)) == [(Connector.NACS, ChargerLevel.DC_FAST, 250.0, 8)]


def test_level2_unit_and_ac_connector_never_marked_dc():
    r = rec(
        id=3,
        ev_connector_types=["J1772"],
        ev_charging_units=[
            {"charging_level": "2", "connectors": {"J1772": {"power_kw": 7.2, "port_count": 2}}},
            {"charging_level": "dc_fast", "connectors": {"J1772": {"power_kw": 19, "port_count": 1}}},
        ],
    )
    levels = {(p.connector, p.level) for p in to_station(r, RETRIEVED).ports}
    assert levels == {(Connector.J1772, ChargerLevel.L2)}


def test_fallback_without_units_has_unknown_power():
    r = rec(id=4, ev_charging_units=None, ev_dc_fast_num=2)
    assert sorted(ports_of(to_station(r, RETRIEVED)), key=str) == sorted(
        [(Connector.CCS1, ChargerLevel.DC_FAST, None, 1), (Connector.CHADEMO, ChargerLevel.DC_FAST, None, 1)],
        key=str,
    )


def test_fallback_tesla_network_is_dc_and_destination_is_ac():
    sc = rec(id=5, ev_network="Tesla", ev_connector_types=["TESLA"], ev_dc_fast_num=12, ev_charging_units=[])
    assert ports_of(to_station(sc, RETRIEVED)) == [(Connector.NACS, ChargerLevel.DC_FAST, None, 12)]
    dest = rec(
        id=6,
        ev_network="Tesla Destination",
        ev_connector_types=["TESLA", "J1772"],
        ev_dc_fast_num=None,
        ev_level2_evse_num=4,
        ev_charging_units=[],
    )
    assert {(p.connector, p.level) for p in to_station(dest, RETRIEVED).ports} == {
        (Connector.NACS, ChargerLevel.L2),
        (Connector.J1772, ChargerLevel.L2),
    }


def test_status_and_access_mapping():
    assert to_station(rec(status_code="T"), RETRIEVED).status.value == "temporarily_unavailable"
    assert to_station(rec(access_code="private"), RETRIEVED).access == "restricted"


@pytest.mark.parametrize(
    "over, reason",
    [
        ({"ev_connector_types": ["NEMA515"], "ev_charging_units": []}, "no_supported_connector"),
        (
            {
                "ev_connector_types": ["J3271"],
                "ev_charging_units": [{"charging_level": "dc_fast", "connectors": {"J3271": {"power_kw": 1000}}}],
            },
            "no_supported_connector",
        ),
        ({"latitude": None}, "missing_coordinates"),
        ({"id": None}, "missing_id"),
    ],
)
def test_unusable_records_are_skipped_and_counted(over, reason):
    stations, report = stations_from_records([rec(), rec(**{"id": 77, **over})], RETRIEVED)
    assert len(stations) == 1 and report.skipped == {reason: 1} and report.converted == 1


@pytest.mark.parametrize(
    "over, fragment",
    [
        ({"ev_connector_types": ["GBT"], "ev_charging_units": []}, "unknown AFDC connector code 'GBT'"),
        ({"ev_charging_units": [{"charging_level": "dc_fast", "connectors": {"CCS2": {}}}]}, "'CCS2'"),
        ({"status_code": "X"}, "unknown status_code"),
        ({"access_code": "members"}, "unknown access_code"),
        ({"ev_charging_units": [{"charging_level": "3", "connectors": {}}]}, "charging_level"),
    ],
)
def test_undocumented_values_fail_loudly(over, fragment):
    with pytest.raises(AfdcSchemaError, match=fragment):
        stations_from_records([rec(**over)], RETRIEVED)


def test_duplicate_ids_are_counted_not_kept_twice():
    stations, report = stations_from_records([rec(), rec()], RETRIEVED)
    assert len(stations) == 1 and report.skipped == {"duplicate_id": 1}


def test_converted_stations_pass_store_validation(tmp_path):
    records = [
        rec(),
        rec(id=4, ev_charging_units=None, ev_dc_fast_num=2),
        rec(id=5, ev_network="Tesla", ev_connector_types=["TESLA"], ev_dc_fast_num=12, ev_charging_units=[]),
    ]
    stations, report = stations_from_records(records, RETRIEVED)
    problems = validate_store(DataStore(stations, [], [], {}, tmp_path))
    assert blocking_problems(problems) == {}
    assert len(problems["dc_power_unknown"]) == report.unknown_power_dc_ports == 3


class _FakeResponse:
    def __init__(self, status, payload):
        self.status_code, self._payload = status, payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


def test_fetch_writes_raw_file_without_the_key(tmp_path, monkeypatch):
    requests = pytest.importorskip("requests")
    seen = {}

    def fake_get(url, params, timeout):
        seen.update(url=url, params=params)
        return _FakeResponse(200, {"total_results": 1, "fuel_stations": [rec()]})

    monkeypatch.setattr(requests, "get", fake_get)
    path = Path(afdc.fetch_stations("SECRET123", state="az", out_dir=str(tmp_path)))
    assert seen["params"]["api_key"] == "SECRET123" and seen["params"]["state"] == "AZ"
    assert seen["params"]["fuel_type"] == "ELEC" and seen["params"]["limit"] == "all"
    text = path.read_text()
    assert "SECRET123" not in text and "api_key" not in text
    stations, report, meta = stations_from_raw(path)
    assert [s.station_id for s in stations] == ["afdc:1001"] and meta["total_results"] == 1


def test_fetch_error_message_hides_the_key(tmp_path, monkeypatch):
    requests = pytest.importorskip("requests")
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(403, {"error": "bad key SECRET123"}))
    with pytest.raises(RuntimeError) as exc:
        afdc.fetch_stations("SECRET123", out_dir=str(tmp_path))
    assert "HTTP 403" in str(exc.value) and "SECRET123" not in str(exc.value)


def test_fetch_requires_a_key(tmp_path):
    with pytest.raises(ValueError, match="NREL_API_KEY"):
        afdc.fetch_stations("", out_dir=str(tmp_path))


def test_duplicate_station_ids_rejected_by_datastore(tmp_path):
    st = to_station(rec(), RETRIEVED)
    with pytest.raises(ValueError, match="duplicate station ids"):
        DataStore([st, st], [], [], {}, tmp_path)


def test_connector_listed_only_at_station_level_is_kept_with_unknown_power():
    r = rec(
        id=10,
        ev_connector_types=["J1772COMBO", "J1772"],
        ev_level2_evse_num=2,
        ev_charging_units=[
            {"charging_level": "dc_fast", "connectors": {"J1772COMBO": {"power_kw": 150, "port_count": 2}}}
        ],
    )
    assert sorted(ports_of(to_station(r, RETRIEVED)), key=str) == sorted(
        [(Connector.CCS1, ChargerLevel.DC_FAST, 150.0, 2), (Connector.J1772, ChargerLevel.L2, None, 2)], key=str
    )


@pytest.mark.parametrize("level", ["dc_fast", "DC Fast", "DC_FAST", "dc-fast"])
def test_charging_level_spelling_variants(level):
    r = rec(ev_charging_units=[{"charging_level": level, "connectors": {"J1772COMBO": {"power_kw": 50}}}])
    assert {p.level for p in to_station(r, RETRIEVED).ports} == {ChargerLevel.DC_FAST}


@pytest.mark.parametrize("level, expected", [("2", ChargerLevel.L2), (2, ChargerLevel.L2), ("Level 2", ChargerLevel.L2),
                                             ("L1", ChargerLevel.L1), ("1", ChargerLevel.L1)])  # fmt: skip
def test_ac_level_variants(level, expected):
    r = rec(
        ev_connector_types=["TESLA"],
        ev_charging_units=[{"charging_level": level, "connectors": {"TESLA": {"power_kw": 11}}}],
    )
    assert {p.level for p in to_station(r, RETRIEVED).ports} == {expected}


def test_odd_port_counts_do_not_crash():
    r = rec(id=11, ev_charging_units=None, ev_dc_fast_num="two", ev_level2_evse_num="", ev_level1_evse_num=None)
    assert {p.count for p in to_station(r, RETRIEVED).ports} == {1}


def test_afdc_urls_use_the_current_domain():
    """developer.nrel.gov was retired on 2026-05-29; requests to it fail with ConnectionError."""
    from clickless.data import afdc

    for url in (afdc.API_URL, afdc.RECORD_URL):
        assert url.startswith("https://developer.nlr.gov/api/alt-fuel-stations/v1")
