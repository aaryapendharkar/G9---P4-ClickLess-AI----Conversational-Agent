from clickless.contracts import (
    AmenityCategory,
    AmenityPreference,
    Connector,
    Place,
    TripRequest,
)


def test_fixtures_load_and_validate(store):
    assert len(store.stations) >= 5 and len(store.vehicles) == 3 and store.amenities
    assert all(s.provenance.source == "SYNTHETIC" for s in store.stations.values())


def test_missing_safety_critical_fields():
    req = TripRequest(request_id="r", raw_text="x", origin=Place(text="Phoenix", lat=1, lon=2))
    assert req.missing_safety_critical() == ["destination", "vehicle_id", "start_soc_pct"]


def test_unresolved_place_counts_as_missing():
    req = TripRequest(request_id="r", raw_text="x", origin=Place(text="Nowhere"))
    assert "origin" in req.missing_safety_critical()


def test_preference_key():
    assert AmenityPreference(category=AmenityCategory.RESTROOM, wheelchair_required=True).key == "restroom_wheelchair"
    assert AmenityPreference(category=AmenityCategory.FOOD).key == "food"


def test_vehicle_supported_connectors(store):
    comet = store.vehicle("fixture-comet-awd-2026")
    assert comet.supported_connectors() == {Connector.NACS, Connector.CCS1}


# ---- contracts v0.4 (ADR 0009): THIRD_PARTY source, backward compatible ----


def test_contracts_version_is_0_4():
    from clickless.contracts import CONTRACTS_VERSION

    assert CONTRACTS_VERSION == "0.4.0"


def test_provenance_sources_v03_still_valid_and_third_party_added():
    from datetime import UTC, datetime

    import pydantic
    import pytest

    from clickless.contracts import DataStatus, Provenance

    now = datetime(2026, 11, 3, tzinfo=UTC)
    for source in ["AFDC", "FUELECONOMY", "MANUFACTURER", "OSM", "OSRM", "DERIVED", "SYNTHETIC", "THIRD_PARTY"]:
        assert Provenance(source=source, retrieved_at=now, status=DataStatus.STATIC).source == source
    with pytest.raises(pydantic.ValidationError):
        Provenance(source="WIKIPEDIA", retrieved_at=now, status=DataStatus.STATIC)
