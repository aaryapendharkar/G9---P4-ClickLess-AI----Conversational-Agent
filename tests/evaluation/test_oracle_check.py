"""check_itinerary: one hand-made itinerary per hard constraint, each violating exactly that one.

World: route 200 km, start 50 %. Stations at km 60 unless stated. A clean plan stops at S
(CCS1 50 kW, on route): arrive 26 %, depart 80 %, arrive at the destination with 24 % (>= 20 %).
"""

import pytest
from _oracle_world import itinerary, off_for_detour, port, req, station, stop, world

from clickless.contracts import Leg
from evaluation.oracle.label_setting import OracleUnavailable

STATIONS = [
    station("S", 60, ports=[port(kw=50)]),
    station("S2", 110, ports=[port(kw=50)]),
    station("X", 60, ports=[port("CHADEMO", kw=50)]),
    station("N2", 60, ports=[port("NACS", "l2", 11)]),
    station("R", 60, ports=[port(kw=50)], access="restricted"),
    station("U", 60, ports=[port(kw=50)], status="temporarily_unavailable"),
    station("D6", 60, off_for_detour(6), ports=[port(kw=50)]),
]


def codes(report):
    return sorted(v.code for v in report.violations)


def check(itin, scope="stop", **request_kw):
    return world(STATIONS, detour_scope=scope).check_itinerary(req(**request_kw), itin)


def test_clean_itinerary_passes():
    report = check(itinerary(stop("S")))
    assert report.ok and report.violations == [] and report.notes == []
    # objectives under the oracle model: 26 -> 80 % = 27 kWh / 50 kW = 32.4 min + 3
    assert report.objectives == pytest.approx((120 + 2 + 35.4, 2.0, 35.4))


@pytest.mark.parametrize(
    "itin,request_kw,expected",
    [
        (itinerary(stop("X", connector="CHADEMO")), {}, ["connector"]),
        (itinerary(stop("N2", connector="NACS", kw=11)), {}, ["connector"]),  # adapter at AC
        (itinerary(stop("S")), {"connectors_override": ["NACS"]}, ["connector"]),  # override narrows
        (itinerary(stop("S")), {"min_power_kw": 100}, ["power"]),
        (itinerary(stop("S", kw=150)), {}, ["power"]),  # claimed power not in data
        (itinerary(stop("D6", detour=6.0)), {"max_detour_min": 5}, ["detour"]),
        (itinerary(stop("S", depart=75)), {}, ["arrival_soc"]),  # arrives with 19 %
        (itinerary(stop("S2", km=110, arrive=6, depart=100)), {}, ["reserve"]),  # arrives at S2 with 6 %
        (itinerary(stop("U")), {}, ["status"]),
        (itinerary(stop("R")), {}, ["access"]),
        (
            itinerary(stop("S2", km=110, arrive=46, depart=90), stop("S", arrive=90, depart=90)),
            {"start": 90},
            ["continuity"],
        ),
        (itinerary(stop("S", depart=101)), {}, ["departure_soc"]),
        (itinerary(stop("S", arrive=82, depart=80)), {}, ["departure_soc"]),  # departs below its own arrival
        (itinerary(stop("NOPE")), {}, ["unknown_station"]),
    ],
)
def test_each_violation_alone(itin, request_kw, expected):
    assert codes(check(itin, **request_kw)) == expected


def test_every_violation_is_listed_not_just_the_first():
    report = check(itinerary(stop("R", depart=75)), min_power_kw=100)
    assert codes(report) == ["access", "arrival_soc", "power"]
    assert not report.ok


def test_reserve_and_arrival_both_listed_when_destination_is_below_reserve():
    assert codes(check(itinerary(stop("S", depart=60)))) == ["arrival_soc", "reserve"]  # arrives with 4 %


def test_total_detour_scope():
    o = world([*STATIONS, station("D6b", 110, off_for_detour(6), ports=[port(kw=50)])])
    two = itinerary(
        stop("D6", detour=6.0, arrive=25.5, depart=60), stop("D6b", km=110, detour=6.0, arrive=39, depart=80)
    )
    assert o.check_itinerary(req(max_detour_min=10), two).ok  # 6 and 6 per stop
    total = o.with_overrides(detour_scope="total").check_itinerary(req(max_detour_min=10), two)
    assert codes(total) == ["detour"] and total.violations[0].stop_index is None


def test_route_distance_from_legs():
    itin = itinerary(stop("S")).model_copy(
        update={
            "legs": [
                Leg(
                    start_label="o",
                    end_label="S",
                    distance_km=150,
                    drive_min=1,
                    energy_kwh=1,
                    soc_start_pct=50,
                    soc_end_pct=26,
                ),
                Leg(
                    start_label="S",
                    end_label="d",
                    distance_km=150,
                    drive_min=1,
                    energy_kwh=1,
                    soc_start_pct=80,
                    soc_end_pct=24,
                ),
            ]
        }
    )
    assert codes(check(itin)) == ["route_distance"]  # 300 km claimed vs 200 km


def test_claim_mismatch_is_a_note_not_a_violation():
    report = check(itinerary(stop("S", detour=9.0, arrive=30)))
    assert report.ok and len(report.notes) == 2


def test_robust_replay_flips_a_marginal_plan():
    o = world(STATIONS)
    itin = itinerary(stop("S", depart=80))  # 24 % nominal; +15 %: 80 - 64.4 = 15.6 %
    assert o.check_itinerary(req(), itin).ok
    assert codes(o.with_overrides(efficiency_penalty=0.15).check_itinerary(req(), itin)) == ["arrival_soc"]


def test_unknown_power_stop_is_flagged_for_sensitivity():
    o = world([station("Q", 60, ports=[port(kw=None)])])
    report = o.check_itinerary(req(), itinerary(stop("Q", kw=None, depart=76)))
    assert report.ok and report.uses_unknown_power
    assert report.objectives[2] == pytest.approx(33.0)  # 26 -> 76 % = 25 kWh at the 50 kW fallback + 3


def test_not_evaluable_request_raises():
    with pytest.raises(OracleUnavailable):
        world(STATIONS).check_itinerary(req(vehicle_id="nope"), itinerary(stop("S")))
