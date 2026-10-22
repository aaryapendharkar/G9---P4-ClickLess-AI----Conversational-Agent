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


@pytest.mark.xfail(raises=NotImplementedError, reason="TODO(Aarya)")
def test_osm_category_mapping():
    from clickless.contracts import AmenityCategory
    assert normalize_category({"amenity": "fast_food"}) == AmenityCategory.FOOD
    assert normalize_category({"amenity": "toilets"}) == AmenityCategory.RESTROOM
    assert normalize_category({"leisure": "park"}) == AmenityCategory.OTHER


@pytest.mark.xfail(raises=NotImplementedError, reason="TODO(Ian, learning swap)")
def test_fixture_store_passes_validation(store):
    problems = validate_store(store)
    assert all(not v for v in problems.values())
