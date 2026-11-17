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


def test_provenance_sources_v03_still_valid_and_third_party_added():
    from datetime import datetime, timezone

    import pydantic
    import pytest

    from clickless.contracts import DataStatus, Provenance

    now = datetime(2026, 11, 3, tzinfo=timezone.utc)
    for source in ["AFDC", "FUELECONOMY", "MANUFACTURER", "OSM", "OSRM", "DERIVED", "SYNTHETIC", "THIRD_PARTY"]:
        assert Provenance(source=source, retrieved_at=now, status=DataStatus.STATIC).source == source
    with pytest.raises(pydantic.ValidationError):
        Provenance(source="WIKIPEDIA", retrieved_at=now, status=DataStatus.STATIC)


# ---- contracts v0.5 (ADR 0015): additive PlanResult evidence ----


def test_contracts_version_is_0_5():
    from clickless.contracts import CONTRACTS_VERSION

    assert CONTRACTS_VERSION == "0.5.0"


def test_plan_result_evidence_is_optional_and_round_trips():
    from datetime import datetime, timezone

    from clickless.contracts import (
        DataStatus,
        Evidence,
        PlanResult,
        Provenance,
    )

    # Results saved before contract v0.5 do not contain an evidence field.
    # They must remain loadable and receive the new empty default.
    legacy_payload = {
        "request_id": "legacy-result",
        "config": "A",
        "status": "error",
    }
    legacy_result = PlanResult.model_validate(legacy_payload)

    assert legacy_result.evidence == []

    station_name_evidence = Evidence(
        evidence_id="kg:FIX-ST-004:name",
        subject_id="FIX-ST-004",
        field="name",
        value="Fixture Camp Verde Hub",
        provenance=Provenance(
            source="SYNTHETIC",
            source_record_id="FIX-ST-004",
            retrieved_at=datetime(2026, 11, 15, tzinfo=timezone.utc),
            status=DataStatus.STATIC,
        ),
    )
    result_with_evidence = legacy_result.model_copy(
        update={"evidence": [station_name_evidence]}
    )

    restored_result = PlanResult.model_validate_json(
        result_with_evidence.model_dump_json()
    )

    assert restored_result.evidence == [station_name_evidence]
