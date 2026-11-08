import copy

import pytest

from clickless.contracts import Connector
from clickless.data.connectors import normalize_afdc_connector
from clickless.data.osm_amenities import normalize_category
from clickless.data.snapshot import verify_manifest, write_manifest
from clickless.data.validate import validate_store


def test_afdc_connector_codes():
    assert normalize_afdc_connector("J1772COMBO") == Connector.CCS1
    assert normalize_afdc_connector("tesla") == Connector.NACS
    assert normalize_afdc_connector("NEMA515") is None


def test_unknown_connector_code_is_loud():
    with pytest.raises(KeyError):
        normalize_afdc_connector("SOMETHING_NEW")


def test_geocode_fixture(store):
    p = store.geocode("phoenix")
    assert p.resolved and p.text == "Phoenix"
    assert not store.geocode("Atlantis").resolved


def test_manifest_roundtrip(tmp_path):
    (tmp_path / "a.json").write_text("{}")
    write_manifest(str(tmp_path))
    assert verify_manifest(str(tmp_path)) == []
    (tmp_path / "a.json").write_text('{"changed": 1}')
    assert verify_manifest(str(tmp_path)) == ["a.json"]


def test_osm_category_mapping():
    from clickless.contracts import AmenityCategory
    assert normalize_category({"amenity": "fast_food"}) == AmenityCategory.FOOD
    assert normalize_category({"amenity": "toilets"}) == AmenityCategory.RESTROOM
    assert normalize_category({"leisure": "park"}) == AmenityCategory.OTHER


def test_fixture_store_passes_validation(store):
    problems = validate_store(store)
    assert all(not v for v in problems.values())


def _first_station(s):
    return next(iter(s.stations.values()))


def _swap_coordinates(s):
    st = _first_station(s)
    st.lat, st.lon = st.lon, st.lat


def _duplicate_amenity(s):
    s.amenities.append(s.amenities[0])


def _remove_ports(s):
    _first_station(s).ports = []


def _drop_dc_power(s):
    st = next(st for st in s.stations.values() if any(p.level.value == "dc_fast" for p in st.ports))
    next(p for p in st.ports if p.level.value == "dc_fast").power_kw = None


def _remove_station_provenance(s):
    _first_station(s).provenance = None


def _remove_vehicle_field_provenance(s):
    next(iter(s.vehicles.values())).field_provenance.pop("usable_kwh")


@pytest.mark.parametrize(
    ("mutate", "check", "expected_fragment"),
    [
        (_swap_coordinates, "coordinates", "outside US bounding boxes"),
        (_duplicate_amenity, "duplicate_ids", "appears 2 times"),
        (_remove_ports, "station_ports", "no ports"),
        (_drop_dc_power, "dc_power_unknown", "no power_kw"),
        (_remove_station_provenance, "provenance", "missing provenance"),
        (_remove_vehicle_field_provenance, "vehicle_field_provenance", "usable_kwh"),
    ],
)
def test_validation_flags_each_problem(store, mutate, check, expected_fragment):
    bad = copy.deepcopy(store)  # the store fixture is session-scoped; never mutate it
    mutate(bad)
    problems = validate_store(bad)
    assert len(problems[check]) == 1 and expected_fragment in problems[check][0]
    assert all(not v for k, v in problems.items() if k != check)
