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
