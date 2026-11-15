"""Split / freeze / double-annotation tests. Synthetic items only -- never the real benchmark."""

import json
import random
from collections import Counter

import pytest

from evaluation.schema import BenchmarkItem
from evaluation.splits import (
    assign_double_annotation,
    freeze,
    normalize_place,
    route_key,
    stratified_split,
    verify_frozen,
)

AUTHORS = ["deepti", "aarya", "manas", "hriday", "ian"]
CHECKER = {"aarya": "manas", "manas": "hriday", "hriday": "deepti", "deepti": "ian", "ian": "aarya"}  # ring
MIX = ["urban"] * 5 + ["rural"] * 5 + ["sparse_charging"] * 4 + ["multi_stop"] * 4 + ["incomplete"] * 4 \
    + ["conflicting_prefs"] * 3 + ["accessibility"] * 3 + ["priority_tradeoff"] * 2  # fmt: skip
ROUTE_TYPES = ["urban", "rural", "mixed"]


def make_items(shared_routes: dict[str, tuple[str, str]] | None = None) -> list[dict]:
    """150 synthetic items (30 per author, the README category mix), each on a unique route
    unless `shared_routes` overrides an item's (origin, destination)."""
    items = []
    for a in AUTHORS:
        for n, cat in enumerate(MIX, 1):
            iid = f"{a}-{n:03d}"
            origin, dest = (shared_routes or {}).get(iid, (f"Town {iid}, AZ", f"City {iid}, NV"))
            items.append({
                "id": iid, "author": a, "cross_checked_by": CHECKER[a], "category": cat,
                "route_type": ROUTE_TYPES[(n + len(a)) % 3], "text": f"synthetic {iid}",
                "gold_request": {"origin": origin, "destination": dest, "start_soc_pct": 50},
                "clarification_required": False,
            })  # fmt: skip
    return items


# Deepti's routes repeated by others: case/punctuation/whitespace variants and reversed direction.
SHARED = {
    "deepti-001": ("Flagstaff, AZ", "Page, AZ"),
    "aarya-006": ("flagstaff az", "PAGE,  AZ"),
    "hriday-011": ("Page AZ", "Flagstaff, A.Z."),  # reversed
    "deepti-002": ("Ely, NV", "Tonopah, NV"),
    "ian-012": ("Tonopah, NV", "Ely, NV"),
    # a non-Deepti shared pair: must stay together, wherever it lands
    "manas-003": ("Phoenix, AZ", "Tucson, AZ"),
    "ian-004": ("Tucson AZ", "phoenix, az"),
    "aarya-020": ("PHOENIX AZ", "Tucson, AZ"),
}


def _ids(items) -> list[str]:
    return [i.id for i in items]


# ---- normalization ---------------------------------------------------------------------------


def test_route_key_is_case_whitespace_punctuation_and_direction_insensitive():
    def item(o, d):
        return BenchmarkItem.model_validate(make_items({"ian-001": (o, d)})[120])

    assert normalize_place("  St. Louis,  MO ") == normalize_place("st louis mo")
    assert route_key(item("Flagstaff, AZ", "Page, AZ")) == route_key(item("page az", "FLAGSTAFF,AZ"))
    assert route_key(item("Flagstaff, AZ", "Page, AZ")) != route_key(item("Flagstaff, AZ", "Sedona, AZ"))


def test_item_without_origin_is_its_own_group():
    items = make_items()
    items[0]["gold_request"].pop("origin")
    items[1]["gold_request"].pop("origin")
    a, b = (BenchmarkItem.model_validate(x) for x in items[:2])
    assert route_key(a) != route_key(b)


# ---- split -----------------------------------------------------------------------------------


def test_split_sizes_and_determinism_regardless_of_input_order():
    items = make_items(SHARED)
    first = stratified_split(items)
    assert len(first.dev) == 50 and len(first.test) == 100
    for s in range(5):
        shuffled = items[:]
        random.Random(s).shuffle(shuffled)
        again = stratified_split(shuffled)
        assert _ids(again.dev) == _ids(first.dev) and _ids(again.test) == _ids(first.test)


def test_seed_changes_the_split():
    items = make_items(SHARED)
    assert _ids(stratified_split(items, seed=573).dev) != _ids(stratified_split(items, seed=1).dev)


def test_every_deepti_item_goes_to_dev():
    result = stratified_split(make_items(SHARED))
    assert {i.id for i in result.dev if i.author == "deepti"} == {f"deepti-{n:03d}" for n in range(1, 31)}
    assert not [i for i in result.test if i.author == "deepti"]


def test_no_origin_destination_pair_in_both_splits():
    result = stratified_split(make_items(SHARED))
    assert not {route_key(i) for i in result.dev} & {route_key(i) for i in result.test}
    # the non-Deepti group stayed together
    group = {"manas-003", "ian-004", "aarya-020"}
    assert group <= set(_ids(result.dev)) or group <= set(_ids(result.test))


def test_guard_pulls_deepti_route_partners_into_dev_and_reports_slots():
    result = stratified_split(make_items(SHARED))
    assert result.guard_ids == ["aarya-006", "hriday-011", "ian-012"]
    assert result.guard_dev_slots == 3
    assert set(result.guard_ids) <= set(_ids(result.dev))


def test_guard_overflow_fails_loudly():
    shared = {f"{a}-{n:03d}": ("Flagstaff, AZ", "Page, AZ") for a in ("deepti", "aarya") for n in range(1, 31)}
    with pytest.raises(ValueError, match="force 60 items into dev"):
        stratified_split(make_items(shared))


def test_test_mix_tracks_overall_mix():
    items = [BenchmarkItem.model_validate(x) for x in make_items(SHARED)]
    test = stratified_split(items).test
    for attr in ("category", "route_type"):
        overall, got = Counter(getattr(i, attr) for i in items), Counter(getattr(i, attr) for i in test)
        for k, n in overall.items():
            assert abs(got[k] - n * 100 / 150) <= 1, (attr, k, got[k], n * 100 / 150)


def test_duplicate_ids_rejected():
    items = make_items()
    items[1]["id"] = items[0]["id"]
    with pytest.raises(ValueError, match="duplicate"):
        stratified_split(items)


# ---- freeze ----------------------------------------------------------------------------------


@pytest.fixture
def frozen(tmp_path):
    result = stratified_split(make_items(SHARED))
    out = tmp_path / "benchmark"
    return result, out, freeze(result.dev, result.test, out_dir=out)


def test_freeze_writes_split_files_and_metadata(frozen):
    result, out, frozen_path = frozen
    meta = json.loads(frozen_path.read_text())
    assert set(meta["files"]) == {"benchmark/requests/dev/dev.jsonl", "benchmark/requests/test/test.jsonl"}
    assert meta["seed"] == 573 and meta["frozen_at"] and "git_commit" in meta
    assert meta["counts"]["dev"]["n"] == 50 and meta["counts"]["test"]["n"] == 100
    assert meta["counts"]["dev"]["author"]["deepti"] == 30 and "deepti" not in meta["counts"]["test"]["author"]
    assert sum(meta["counts"]["test"]["category"].values()) == 100
    lines = (out / "requests/test/test.jsonl").read_text().splitlines()
    assert [json.loads(x)["id"] for x in lines] == _ids(result.test)
    verify_frozen(out)


def test_hash_check_fails_when_test_file_changes(frozen):
    _, out, _ = frozen
    test_file = out / "requests/test/test.jsonl"
    test_file.write_bytes(test_file.read_bytes().replace(b"synthetic", b"edited", 1))
    with pytest.raises(AssertionError, match="test.jsonl changed after freeze"):
        verify_frozen(out)


def test_freeze_refuses_to_overwrite(frozen):
    result, out, frozen_path = frozen
    before = frozen_path.read_bytes()
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        freeze(result.dev, result.test, out_dir=out)
    assert frozen_path.read_bytes() == before


def test_freeze_refuses_if_only_the_test_file_exists(tmp_path):
    result = stratified_split(make_items(SHARED))
    (tmp_path / "requests/test").mkdir(parents=True)
    (tmp_path / "requests/test/test.jsonl").write_text("")
    with pytest.raises(FileExistsError):
        freeze(result.dev, result.test, out_dir=tmp_path)


def test_freeze_requires_exactly_150_items(tmp_path):
    items = make_items(SHARED)
    items = [x for x in items if x["id"] != "manas-030"]
    result = stratified_split(items)
    with pytest.raises(ValueError, match="exactly 150 valid items, got 149"):
        freeze(result.dev, result.test, out_dir=tmp_path)
    assert not list(tmp_path.iterdir())  # nothing written


def test_freeze_rejects_invalid_items(tmp_path):
    result = stratified_split(make_items(SHARED))
    bad = result.test[0].model_dump()
    bad["category"] = "not-a-category"
    with pytest.raises(ValueError):
        freeze(result.dev, [bad] + result.test[1:], out_dir=tmp_path)
    assert not list(tmp_path.iterdir())


def test_freeze_requires_cross_checks(tmp_path):
    items = make_items(SHARED)
    items[42]["cross_checked_by"] = None
    result = stratified_split(items)
    with pytest.raises(ValueError, match="cross_checked_by null"):
        freeze(result.dev, result.test, out_dir=tmp_path)
    assert not list(tmp_path.iterdir())


def test_freeze_rejects_leaky_or_rule_breaking_splits(tmp_path):
    result = stratified_split(make_items(SHARED))
    deepti = next(i for i in result.dev if i.author == "deepti")
    other = result.test[0]
    dev = [i for i in result.dev if i is not deepti] + [other]
    test = [deepti] + result.test[1:]
    with pytest.raises(ValueError, match="deepti items in test"):
        freeze(dev, test, out_dir=tmp_path)


# ---- double annotation -----------------------------------------------------------------------


def test_double_annotation_constraints():
    items = {x["id"]: BenchmarkItem.model_validate(x) for x in make_items(SHARED)}
    pairs = assign_double_annotation(list(items.values()))
    assert len(pairs) == 40 and len({p[0] for p in pairs}) == 40
    assert Counter(items[i].author for i, _ in pairs) == {a: 8 for a in AUTHORS}
    for item_id, second in pairs:
        it = items[item_id]
        assert second in AUTHORS and second not in (it.author, it.cross_checked_by)
    for a in AUTHORS:  # stratified: 8 items across the 8 categories -> one each
        assert sorted(items[i].category for i, _ in pairs if items[i].author == a) == sorted(set(MIX))
    assert Counter(s for _, s in pairs) == {a: 8 for a in AUTHORS}  # balanced load


def test_double_annotation_is_deterministic_and_does_not_modify_items():
    items = make_items(SHARED)
    snapshot = json.dumps(items, sort_keys=True)
    shuffled = items[:]
    random.Random(7).shuffle(shuffled)
    assert assign_double_annotation(items) == assign_double_annotation(shuffled)
    assert json.dumps(items, sort_keys=True) == snapshot
    assert not any(x.get("double_annotated") for x in items)


def test_double_annotation_requires_cross_checks():
    items = make_items()
    items[3]["cross_checked_by"] = None
    with pytest.raises(ValueError, match="cross-check"):
        assign_double_annotation(items)
