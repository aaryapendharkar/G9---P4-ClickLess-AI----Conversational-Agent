"""Curated vehicle table (issue #28, ADR 0009): loads, is safe, keeps per-field provenance."""

import csv
import math
from datetime import date

import pytest

from clickless.config import repo_path
from clickless.contracts import Connector
from clickless.data import DataStore
from clickless.data.validate import provenance_completeness, validate_store
from clickless.data.vehicles import COLUMNS, CuratedVehicleError, load_curated_vehicles
from evaluation.schema import load_items

CSV_PATH = repo_path("data/curated/vehicles.csv")
CCS1, NACS, CHADEMO, J1772 = Connector.CCS1, Connector.NACS, Connector.CHADEMO, Connector.J1772

# vehicle_id -> (usable_kwh, max_dc_kw, native, adapters). Mirrors data/curated/vehicles.csv; a
# change to either side must be deliberate (the benchmark freeze depends on these values).
EXPECTED = {
    "tesla-model-3-lr-awd-2024": (75.0, 250, [NACS], [CCS1]),
    "tesla-model-3-rwd-2024": (57.5, 170, [NACS], [CCS1]),
    "tesla-model-y-lr-awd-2024": (75.0, 250, [NACS], [CCS1]),
    "chevrolet-bolt-euv-2023": (65.0, 55, [CCS1], [NACS]),
    "chevrolet-equinox-ev-fwd-2024": (85.0, 150, [CCS1], [NACS]),
    "ford-mustang-mach-e-er-awd-2024": (91.0, 150, [CCS1], [NACS]),
    "ford-f-150-lightning-er-2024": (131.0, 150, [CCS1], [NACS]),
    "hyundai-ioniq-5-lr-rwd-2024": (74.0, 233, [CCS1], [NACS]),
    "hyundai-ioniq-6-lr-rwd-2024": (74.0, 233, [CCS1], [NACS]),
    "kia-ev6-lr-rwd-2024": (74.0, 240, [CCS1], [NACS]),
    "kia-ev9-lr-rwd-2024": (96.0, 210, [CCS1], [NACS]),
    "nissan-leaf-sv-plus-2024": (59.0, 100, [CHADEMO, J1772], []),
    "rivian-r1s-dual-large-2023": (131.0, 220, [CCS1], [NACS]),
    "volkswagen-id4-pro-rwd-2024": (77.0, 175, [CCS1], [NACS]),
    "bmw-i4-edrive40-2024": (80.7, 205, [CCS1], [NACS]),
}


@pytest.fixture(scope="module")
def vehicles():
    return {v.vehicle_id: v for v in load_curated_vehicles()}


@pytest.fixture(scope="module")
def csv_rows():
    with open(CSV_PATH, newline="") as f:
        return {r["vehicle_id"]: r for r in csv.DictReader(f)}


@pytest.fixture(scope="module")
def curated_store(vehicles, tmp_path_factory):
    return DataStore([], list(vehicles.values()), [], {}, tmp_path_factory.mktemp("routes"))


# ------------------------------------------------------------------ the 15 rows


def test_all_fifteen_vehicles_load(vehicles):
    assert set(vehicles) == set(EXPECTED)


@pytest.mark.parametrize("vid", sorted(EXPECTED))
def test_values_connectors_and_adapters(vehicles, vid):
    v = vehicles[vid]
    usable, dc, native, adapters = EXPECTED[vid]
    assert v.usable_kwh == usable
    assert v.max_dc_kw == dc
    assert v.native_connectors == native
    assert v.adapters == adapters
    assert 0.08 <= v.efficiency_kwh_per_km <= 0.60
    assert v.aliases and all(a == a.lower().strip() for a in v.aliases)


@pytest.mark.parametrize("vid", sorted(EXPECTED))
def test_field_level_provenance(vehicles, csv_rows, vid):
    v, row = vehicles[vid], csv_rows[vid]
    expected_fields = {"usable_kwh", "efficiency_kwh_per_km", "max_dc_kw"} | ({"adapters"} if v.adapters else set())
    assert set(v.field_provenance) == expected_fields
    for field, url_col, type_col in [
        ("usable_kwh", "usable_kwh_source_url", "usable_kwh_source_type"),
        ("efficiency_kwh_per_km", "efficiency_source_url", "efficiency_source_type"),
        ("max_dc_kw", "max_dc_source_url", "max_dc_source_type"),
        ("adapters", "adapter_source_url", "adapter_source_type"),
    ]:
        if field not in expected_fields:
            continue
        prov = v.field_provenance[field]
        assert prov.source == row[type_col]
        assert prov.source_url == row[url_col]
        assert prov.status.value == "static"
        assert prov.retrieved_at.date() == date.fromisoformat(row["accessed_on"])
    # Efficiency is always the EPA rating (proposal §II).
    assert v.field_provenance["efficiency_kwh_per_km"].source == "FUELECONOMY"


def test_nominal_capacity_is_not_used_as_usable(vehicles, csv_rows):
    assert {r["usable_kwh_basis"] for r in csv_rows.values()} == {"usable"}
    # Nissan sells the LEAF SV PLUS pack as "60 kWh" (62 kWh nominal); 59.0 kWh is usable.
    leaf = vehicles["nissan-leaf-sv-plus-2024"]
    assert leaf.usable_kwh == 59.0
    assert leaf.field_provenance["usable_kwh"].source == "THIRD_PARTY"


def test_curated_vehicles_pass_store_validation(curated_store):
    problems = validate_store(curated_store)
    assert all(not p for p in problems.values()), problems
    shares = provenance_completeness(curated_store)
    assert shares["required_fields_share"] == 1.0
    assert math.isclose(shares["source_updated_at_share"], 0.0)  # static table: no upstream update dates


# ------------------------------------------------------------------ aliases and the benchmark


@pytest.mark.parametrize(
    "text, expected",
    [
        ("my 2024 model 3 rwd", ["tesla-model-3-rwd-2024"]),
        ("2024 model 3 long range awd", ["tesla-model-3-lr-awd-2024"]),
        ("my model 3", ["tesla-model-3-lr-awd-2024", "tesla-model-3-rwd-2024"]),  # ambiguous on purpose
        ("my tesla", []),
    ("my 2024 tesla model 3 rwd", ["tesla-model-3-rwd-2024"]),  # hriday-001: brand + variant
    ("tesla model 3 long range", ["tesla-model-3-lr-awd-2024"]),
    ("my tesla model 3", ["tesla-model-3-lr-awd-2024", "tesla-model-3-rwd-2024"]),  # still ambiguous
    ("tesla model y long range", ["tesla-model-y-lr-awd-2024"]),
    ("traded my model 3 for an ioniq 5 long range", ["hyundai-ioniq-5-lr-rwd-2024"]),
        ("2024 bmw i4 edrive40", ["bmw-i4-edrive40-2024"]),
        ("route i40 east", []),  # whole words only
        ("ioniq 5 long range", ["hyundai-ioniq-5-lr-rwd-2024"]),
        ("ioniq 6", ["hyundai-ioniq-6-lr-rwd-2024"]),
        ("my mach-e extended range", ["ford-mustang-mach-e-er-awd-2024"]),
        ("the leaf sv plus", ["nissan-leaf-sv-plus-2024"]),
    ],
)
def test_alias_resolution(curated_store, text, expected):
    assert sorted(v.vehicle_id for v in curated_store.find_vehicles(text)) == expected


def test_no_alias_is_shared_except_documented_ambiguity(vehicles):
    owners: dict[str, set[str]] = {}
    for v in vehicles.values():
        for a in v.aliases:
            owners.setdefault(a, set()).add(v.vehicle_id)
    shared = {a: ids for a, ids in owners.items() if len(ids) > 1}
    # "my Model 3" / "my Tesla Model 3" must trigger clarification (annotation guide rule 2).
    assert shared == {
        "model 3": {"tesla-model-3-lr-awd-2024", "tesla-model-3-rwd-2024"},
        "tesla model 3": {"tesla-model-3-lr-awd-2024", "tesla-model-3-rwd-2024"},
    }


def _raw_items():
    return load_items(repo_path("benchmark/requests/raw"))


def test_every_benchmark_vehicle_id_resolves(vehicles):
    ids = {i.gold_request["vehicle_id"] for i in _raw_items() if "vehicle_id" in i.gold_request}
    assert ids <= set(vehicles), sorted(ids - set(vehicles))


def test_benchmark_texts_resolve_to_their_gold_vehicle(curated_store):
    for item in _raw_items():
        hits = [v.vehicle_id for v in curated_store.find_vehicles(item.text)]
        if "vehicle_id" in item.missing_safety_fields:
            assert len(hits) != 1, (item.id, hits)  # missing/ambiguous must not silently resolve
        elif "vehicle_id" in item.gold_request:
            assert hits == [item.gold_request["vehicle_id"]], (item.id, hits)


# ------------------------------------------------------------------ malformed input fails loudly


def _write(tmp_path, header, rows):
    p = tmp_path / "vehicles.csv"
    with open(p, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
    return p


def _good_rows():
    with open(CSV_PATH, newline="") as f:
        r = csv.reader(f)
        header = next(r)
        return header, [list(row) for row in r]


def _with(column, value, row_index=0):
    header, rows = _good_rows()
    rows[row_index][header.index(column)] = value
    return header, rows


@pytest.mark.parametrize(
    "column, value, message",
    [
        ("native_connectors", "NACS;TYPE2", "unknown connector 'TYPE2'"),
        ("adapters", "CCS2", "unknown connector 'CCS2'"),
        ("adapters", "NACS", "already native"),
        ("usable_kwh_source_type", "WIKIPEDIA", "unknown source type 'WIKIPEDIA'"),
        ("max_dc_source_type", "manufacturer", "unknown source type 'manufacturer'"),
        ("efficiency_source_type", "FUELECONOMY", None),  # valid: control case
        ("usable_kwh_basis", "nominal", "never load nominal/gross capacity"),
        ("usable_kwh_basis", "", "only usable capacity"),
        ("usable_kwh", "75000", "outside plausible range"),
        ("usable_kwh", "seventy", "not a number"),
        ("usable_kwh", "", "required value is empty"),
        ("efficiency_kwh_per_km", "0.26", None),  # still in range: control case
        ("efficiency_kwh_per_km", "26", "outside plausible range"),
        ("model_year", "24", "outside"),
        ("vehicle_id", "Tesla Model 3", "lowercase"),
        ("usable_kwh_source_url", "http://example.com", "https source URL"),
        ("efficiency_source_url", "https://example.com/epa", "must be on fueleconomy.gov"),
        ("aliases", "model 3;;m3", "empty alias"),
        ("aliases", "Model 3", "lowercase"),
        ("accessed_on", "Nov 2", "ISO date"),
    ],
)
def test_bad_cell_is_rejected(tmp_path, column, value, message):
    header, rows = _with(column, value)
    path = _write(tmp_path, header, rows)
    if message is None:
        assert len(load_curated_vehicles(path)) == len(EXPECTED)
        return
    with pytest.raises(CuratedVehicleError, match=message) as exc:
        load_curated_vehicles(path)
    assert f"{path}:2" in str(exc.value) and column in str(exc.value)


def test_adapter_source_without_adapters_is_rejected(tmp_path):
    header, rows = _good_rows()
    leaf = next(r for r in rows if r[0] == "nissan-leaf-sv-plus-2024")
    leaf[header.index("adapter_source_url")] = "https://example.com"
    with pytest.raises(CuratedVehicleError, match="source given but adapters is empty"):
        load_curated_vehicles(_write(tmp_path, header, rows))


def test_adapters_without_source_are_rejected(tmp_path):
    header, rows = _with("adapter_source_url", "")
    with pytest.raises(CuratedVehicleError, match="https source URL"):
        load_curated_vehicles(_write(tmp_path, header, rows))


def test_duplicate_vehicle_id_is_rejected(tmp_path):
    header, rows = _good_rows()
    with pytest.raises(CuratedVehicleError, match="duplicate vehicle_id"):
        load_curated_vehicles(_write(tmp_path, header, rows + [rows[0]]))


def test_header_must_match_exactly(tmp_path):
    header, rows = _good_rows()
    i = header.index("usable_kwh_basis")
    bad_header = header[:i] + header[i + 1 :]
    bad_rows = [r[:i] + r[i + 1 :] for r in rows]
    with pytest.raises(CuratedVehicleError, match="missing=\\['usable_kwh_basis'\\]"):
        load_curated_vehicles(_write(tmp_path, bad_header, bad_rows))
    assert tuple(header) == COLUMNS


def test_wrong_field_count_is_rejected(tmp_path):
    header, rows = _good_rows()
    rows[0] = rows[0] + ["extra"]
    with pytest.raises(CuratedVehicleError, match="wrong number of fields"):
        load_curated_vehicles(_write(tmp_path, header, rows))


def test_empty_table_is_rejected(tmp_path):
    header, _ = _good_rows()
    with pytest.raises(CuratedVehicleError, match="no vehicle rows"):
        load_curated_vehicles(_write(tmp_path, header, []))
