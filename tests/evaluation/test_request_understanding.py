"""Hand-computed examples for the RQ1 request-understanding metrics."""

import math

import pytest

from clickless.contracts import PlanResult, TripRequest
from clickless.contracts.request import AmenityPreference, Place
from evaluation.metrics.request_understanding import (
    city_level_place_prf,
    clarification_prf,
    extraction_prf,
    field_counts,
    missing_field_match,
    parse_place,
    silent_assumption_rate,
    understanding_cis,
)
from evaluation.schema import BenchmarkItem


def item(i, gold, clar=False, missing=()):
    return BenchmarkItem(id=f"t-{i}", author="ian", category="urban", route_type="urban", text="x",
                         gold_request=gold, clarification_required=clar, missing_safety_fields=list(missing))


def result(i, status="ok", **fields):
    return PlanResult(request_id=f"t-{i}", config="A", status=status,
                      request=TripRequest(request_id=f"t-{i}", raw_text="x", **fields))


# Item 1: origin TP (normalised), destination wrong (FP+FN), vehicle TP, start wrong (FP+FN),
#         arrival predicted but not in gold (FP), amenities {food} TP, restroom wheelchair flag wrong
#         (FP for the predicted tuple, FN for the gold tuple).
# Item 2: origin/destination TP, vehicle missing (FN), start TP, connectors TP (order ignored),
#         tradeoff_profile "fastest" in gold, prediction left the default "balanced" (FN).
GOLD_1 = {"origin": "Phoenix, AZ", "destination": "Flagstaff", "vehicle_id": "v1", "start_soc_pct": 65,
          "amenities": [{"category": "food"}, {"category": "restroom", "wheelchair_required": True}]}
GOLD_2 = {"origin": "Tempe", "destination": "Mesa", "vehicle_id": "v2", "start_soc_pct": 40,
          "connectors_override": ["CCS1", "NACS"], "tradeoff_profile": "fastest"}


@pytest.fixture
def pair():
    items = [item(1, GOLD_1), item(2, GOLD_2)]
    results = [
        result(1, origin=Place(text="  phoenix,   arizona "), destination=Place(text="Sedona"), vehicle_id="v1",
               start_soc_pct=60, arrival_soc_pct_min=20,
               amenities=[AmenityPreference(category="food", phrase="lunch"),
                          AmenityPreference(category="restroom")]),
        result(2, origin=Place(text="TEMPE"), destination=Place(text="Mesa."), start_soc_pct=40.0,
               connectors_override=["NACS", "CCS1"]),
    ]
    return items, results


def test_extraction_micro_and_per_field(pair):
    m = extraction_prf(*pair)
    # TP = origin 2 + dest 1 + vehicle 1 + start 1 + amenities 1 + connectors 1 = 7
    # FP = dest 1 + start 1 + arrival 1 + amenities 1 = 4
    # FN = dest 1 + vehicle 1 + start 1 + amenities 1 + tradeoff 1 = 5
    assert m["micro_precision"] == pytest.approx(7 / 11)
    assert m["micro_recall"] == pytest.approx(7 / 12)
    assert m["micro_f1"] == pytest.approx(14 / 23)
    assert m["exact_match"] == 0.0 and m["n"] == 2
    assert (m["origin.tp"], m["origin.fp"], m["origin.fn"]) == (2, 0, 0)
    assert m["origin.f1"] == 1.0
    assert (m["destination.precision"], m["destination.recall"], m["destination.f1"]) == (0.5, 0.5, 0.5)
    assert (m["vehicle_id.precision"], m["vehicle_id.recall"]) == (1.0, 0.5)
    assert m["vehicle_id.f1"] == pytest.approx(2 / 3)
    assert (m["start_soc_pct.tp"], m["start_soc_pct.fp"], m["start_soc_pct.fn"]) == (1, 1, 1)
    assert m["arrival_soc_pct_min.precision"] == 0.0 and math.isnan(m["arrival_soc_pct_min.recall"])
    assert m["arrival_soc_pct_min.f1"] == 0.0
    assert (m["amenities.tp"], m["amenities.fp"], m["amenities.fn"]) == (1, 1, 1)
    assert m["connectors_override.f1"] == 1.0
    assert math.isnan(m["tradeoff_profile.precision"]) and m["tradeoff_profile.recall"] == 0.0
    assert all(math.isnan(m[f"min_power_kw.{k}"]) for k in ("precision", "recall", "f1"))


def test_field_count_rules():
    assert field_counts("vehicle_id", "a", "a") == (1, 0, 0)
    assert field_counts("vehicle_id", "a", "A") == (0, 1, 1)  # vehicle ids are exact
    assert field_counts("start_soc_pct", 50, None) == (0, 0, 1)
    assert field_counts("start_soc_pct", None, 50.0) == (0, 1, 0)
    assert field_counts("start_soc_pct", None, None) == (0, 0, 0)
    assert field_counts("start_soc_pct", 50, 50.5) == (0, 1, 1)  # numbers are exact
    assert field_counts("destination", "St. Louis, MO", "st louis, mo") == (1, 0, 0)
    assert field_counts("connectors_override", ["CCS1"], ["CCS1", "NACS"]) == (0, 1, 1)
    assert field_counts("connectors_override", None, []) == (0, 0, 0)
    assert field_counts("tradeoff_profile", None, "balanced") == (0, 0, 0)
    # amenity priority is part of the compared tuple
    gold = [{"category": "food", "priority": "important"}, {"category": "coffee"}]
    pred = [AmenityPreference(category="food"), AmenityPreference(category="coffee", phrase="latte")]
    assert field_counts("amenities", gold, pred) == (1, 1, 1)


def test_exact_match_counts_both_absent_as_match():
    items = [item(1, {"origin": "A", "destination": "B"}), item(2, {"origin": "A"})]
    results = [result(1, origin=Place(text="a"), destination=Place(text="b")), result(2, vehicle_id="v")]
    assert extraction_prf(items, results)["exact_match"] == 0.5


def test_missing_request_is_all_false_negatives():
    items = [item(1, {"origin": "A", "start_soc_pct": 10})]
    res = PlanResult(request_id="t-1", config="A", status="error")
    m = extraction_prf(items, [res])
    assert m["micro_recall"] == 0.0 and math.isnan(m["micro_precision"]) and m["micro_f1"] == 0.0


def test_mismatched_ids_raise():
    with pytest.raises(ValueError):
        extraction_prf([item(1, {})], [result(2)])


CLAR = [(True, "rejected_incomplete"), (True, "ok"), (False, "needs_clarification"), (False, "ok"),
        (True, "needs_clarification")]


def test_clarification_prf_and_silent_assumption():
    items = [item(i, {}, clar=c) for i, (c, _) in enumerate(CLAR)]
    results = [result(i, status=s) for i, (_, s) in enumerate(CLAR)]
    m = clarification_prf(items, results)
    assert (m["tp"], m["fp"], m["fn"], m["tn"]) == (2, 1, 1, 1)
    assert m["precision"] == pytest.approx(2 / 3) and m["recall"] == pytest.approx(2 / 3)
    assert m["f1"] == pytest.approx(2 / 3)
    assert silent_assumption_rate(items, results)["silent_assumption_rate"] == pytest.approx(1 / 3)


def test_missing_field_match():
    resolved = {"origin": Place(text="a", lat=1, lon=1), "destination": Place(text="b", lat=2, lon=2)}
    items = [
        item(1, {}, clar=True, missing=["start_soc_pct"]),
        item(2, {}, clar=True, missing=["vehicle_id"]),
        item(3, {}),
    ]
    results = [
        result(1, status="rejected_incomplete", vehicle_id="v", **resolved),  # start missing -> match
        result(2, status="rejected_incomplete", **resolved),  # vehicle AND start missing -> no match
        result(3, vehicle_id="v", start_soc_pct=50, **resolved),  # nothing missing -> match
    ]
    m = missing_field_match(items, results)
    assert m["missing_field_exact_match"] == pytest.approx(2 / 3)
    assert m["missing_field_exact_match_incomplete"] == 0.5
    assert (m["n"], m["n_incomplete"]) == (3, 2)


def test_cis_bracket_point_estimates(pair):
    items, results = pair
    items, results = items * 5, [r for _ in range(5) for r in results]  # ids repeat; pairing is positional
    ci = understanding_cis(items, results, n=200)
    m = extraction_prf(items, results)
    assert ci["city_level.origin.f1"] == [1.0, 1.0]
    lo, hi = ci["micro_f1"]
    assert lo <= m["micro_f1"] <= hi
    assert ci["origin.f1"] == [1.0, 1.0]
    assert ci["exact_match"] == [0.0, 0.0]
    assert all(math.isnan(x) for x in ci["silent_assumption_rate"])  # no incomplete items
    assert repr(understanding_cis(items, results, n=200)) == repr(ci)  # fixed seed -> deterministic


# --------------------------------------------------------------------------- places


@pytest.mark.parametrize(
    "text, key",
    [
        ("Flagstaff, AZ", ("flagstaff", "AZ")),
        ("flagstaff, arizona", ("flagstaff", "AZ")),
        ("Flagstaff Arizona", ("flagstaff", "AZ")),  # full name without a comma
        ("Bend OR", ("bend", "OR")),  # capitalised code without a comma
        ("downtown la", ("downtownla", None)),  # lower-case code without a comma: not a state
        ("Downtown Detroit, MI", ("downtowndetroit", "MI")),
        ("Brooklyn, New York", ("brooklyn", "NY")),
        ("New York", ("newyork", None)),  # a bare state name is the place itself
        ("Washington, DC", ("washington", "DC")),
        ("Georgetown, District of Columbia", ("georgetown", "DC")),
        ("Tempe Town Lake, Tempe, AZ", ("tempetownlaketempe", "AZ")),
        ("Phoenix, Sky Harbor", ("phoenixskyharbor", None)),  # tail after the comma is not a state
        ("  ", None),
    ],
)
def test_parse_place(text, key):
    assert parse_place(text) == key


def test_place_strict_vs_city_level():
    # strict (primary): state names and codes are interchangeable, but a missing state is a mismatch
    assert field_counts("origin", "Flagstaff, AZ", "flagstaff arizona") == (1, 0, 0)
    assert field_counts("origin", "Flagstaff, AZ", "Flagstaff") == (0, 1, 1)
    # city level (secondary): a missing state on either side is ignored, a different state is not
    assert field_counts("origin", "Flagstaff, AZ", "Flagstaff", level="city") == (1, 0, 0)
    assert field_counts("origin", "Flagstaff", "Flagstaff, AZ", level="city") == (1, 0, 0)
    assert field_counts("origin", "Portland, OR", "Portland, ME", level="city") == (0, 1, 1)
    assert field_counts("origin", "Portland, OR", "Salem", level="city") == (0, 1, 1)
    # level only affects place fields
    assert field_counts("vehicle_id", "a", "A", level="city") == (0, 1, 1)


def test_city_level_place_prf_reported_separately():
    items = [item(1, {"origin": "Downtown Phoenix, AZ", "destination": "Tempe, Arizona"}),
             item(2, {"origin": "Bend, OR", "destination": "Salem, OR"})]
    results = [result(1, origin=Place(text="downtown phoenix"), destination=Place(text="Tempe, AZ")),
               result(2, origin=Place(text="Bend, ME"), destination=None)]
    strict = extraction_prf(items, results)
    city = city_level_place_prf(items, results)
    # strict: origin 0 TP (state missing, wrong state); destination 1 TP, 1 FN
    assert (strict["origin.tp"], strict["origin.fp"], strict["origin.fn"]) == (0, 2, 2)
    assert (strict["destination.tp"], strict["destination.fp"], strict["destination.fn"]) == (1, 0, 1)
    # city level: item-1 origin now matches; Bend OR vs ME still does not
    assert (city["origin.tp"], city["origin.fp"], city["origin.fn"]) == (1, 1, 1)
    assert city["origin.f1"] == 0.5
    assert (city["places.tp"], city["places.fp"], city["places.fn"]) == (2, 1, 2)
    assert city["places.f1"] == pytest.approx(4 / 7)
    assert city["n"] == 2


def test_lowercase_state_code_uses_gold_hint():
    # a lower-case trailing code counts as the state only when it equals the gold's state code
    assert parse_place("phoenix az", state_hint="AZ") == ("phoenix", "AZ")
    assert parse_place("phoenix az") == ("phoenixaz", None)
    assert field_counts("origin", "Phoenix, AZ", "phoenix az") == (1, 0, 0)
    assert field_counts("origin", "Phoenix, AZ", Place(text="phoenix az")) == (1, 0, 0)
    # "la" is never Louisiana when the gold is in California: no state is inferred, city text differs
    assert parse_place("downtown la", state_hint="CA") == ("downtownla", None)
    assert field_counts("origin", "Downtown Los Angeles, CA", "downtown la") == (0, 1, 1)
    assert field_counts("origin", "Downtown Los Angeles, CA", "downtown la", level="city") == (0, 1, 1)
    # a mismatching lower-case code stays part of the city text rather than becoming a wrong state
    assert field_counts("origin", "Bend, OR", "bend wa", level="city") == (0, 1, 1)
    # gold without a state gives no hint
    assert field_counts("origin", "Phoenix", "phoenix az") == (0, 1, 1)
