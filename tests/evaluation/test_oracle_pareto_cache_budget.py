"""Pareto cache (per request and settings) and the per-search time budget.

The cache must not change any answer: callers get copies, different settings never share an entry,
and a changed store is never served from it. The budget stops the search with complete=False; the
planning metrics then emit oracle_search_incomplete and leave the item out of front-based metrics.
"""

from __future__ import annotations

import pytest
from _oracle_world import ENERGY, itinerary, off_for_detour, port, req, result, station, stop, world
from test_planning_metrics import item

from clickless.config import load_config
from clickless.data.loaders import DataStore
from evaluation.metrics.planning import planning_metrics, unknown_power_sensitivity, unknown_status_reliance
from evaluation.oracle.label_setting import LabelSettingOracle, OracleConfig


@pytest.fixture
def searches(monkeypatch):
    """Counts actual Pareto searches (cache misses), per oracle method."""
    calls = {"indexed": 0, "exhaustive": 0}
    for name in ("_pareto", "_pareto_fast"):  # reference search (exhaustive) and fast search (indexed)
        real = getattr(LabelSettingOracle, name)

        def counting(self, request, real=real):
            calls[self.cfg.method] += 1
            return real(self, request)

        monkeypatch.setattr(LabelSettingOracle, name, counting)
    return calls


def two_station_world(**cfg):
    # U: fast but off-route, K: slow, on route -> a two-plan front (as in test_unknown_status_reliance).
    return world([station("U", 60, off_for_detour(6), status="unknown"), station("K", 60, ports=[port(kw=50)])], **cfg)


# --------------------------------------------------------------------------- cache


def test_same_request_and_settings_search_once_and_return_copies(searches):
    o = two_station_world().fast
    first = o.pareto_plans(req(start=50))
    second = o.pareto_plans(req(start=50))
    assert searches["indexed"] == 1
    assert first == second and first is not second and first.plans is not second.plans
    first.plans.clear()  # a caller mutating its result cannot change the cached one
    assert o.pareto_plans(req(start=50)) == second and searches["indexed"] == 1


def test_different_request_or_settings_search_again(searches):
    o = two_station_world().fast
    o.pareto_plans(req(start=50))
    o.pareto_plans(req(start=55))  # different request
    assert searches["indexed"] == 2
    same = o.with_overrides()  # same settings: shares the cache
    same.pareto_plans(req(start=50))
    assert searches["indexed"] == 2
    robust = o.with_overrides(efficiency_penalty=0.15)  # different settings: its own entry
    a, b = robust.pareto_plans(req(start=50)), o.pareto_plans(req(start=50))
    assert searches["indexed"] == 3 and a != b


def test_cache_follows_a_replaced_store(searches):
    o = two_station_world().fast
    before = o.pareto_plans(req(start=50))
    o.store.stations = {"K": o.store.stations["K"]}  # U removed
    after = o.pareto_plans(req(start=50))
    assert searches["indexed"] == 2
    assert {s for p in before.plans for s in p.station_ids} == {"U", "K"}
    assert {s for p in after.plans for s in p.station_ids} == {"K"}


def test_exhaustive_path_never_caches(searches):
    o = two_station_world().slow
    o.pareto_plans(req(start=50))
    o.pareto_plans(req(start=50))
    assert searches["exhaustive"] == 2


def test_cached_answers_equal_fresh_ones():
    # TwinOracle already compares indexed (fresh), indexed (cached) and exhaustive on every call.
    o = two_station_world()
    for start in (5, 30, 50, 55, 90):
        o.pareto_plans(req(start=start))
        o.pareto_plans(req(start=start))


# --------------------------------------------------------------------------- budget


def test_budget_stops_the_search_with_complete_false():
    o = two_station_world(pareto_time_budget_s=0.0)  # both paths: the budget is part of the model settings
    front = o.pareto_plans(req(start=50))
    assert front.complete is False and front.incomplete_reason == "time_budget" and front.plans == []
    # A rejected request is answered before searching, so the budget does not apply.
    rejected = o.pareto_plans(req(start=5))
    assert rejected.complete is True and rejected.feasibility.reason == "start_below_reserve"


def test_no_budget_and_default_budget_complete():
    for budget in (None, 600.0):
        front = two_station_world(pareto_time_budget_s=budget).pareto_plans(req(start=50))
        assert front.complete is True and front.incomplete_reason is None and len(front.plans) == 2


def test_max_labels_reason_is_kept_distinct():
    front = two_station_world(max_labels=1).pareto_plans(req(start=50))
    assert front.complete is False and front.incomplete_reason == "max_labels"


def test_limits_come_from_configs_evaluation_yaml():
    store = DataStore([], [], [], {}, ".")
    cfg = load_config("A")
    oracle_cfg = cfg["evaluation"]["oracle"]
    built = LabelSettingOracle.from_config(cfg, store=store).cfg
    assert built.max_labels == int(oracle_cfg["max_labels"])  # the deterministic limit
    assert built.pareto_time_budget_s == float(oracle_cfg["pareto_time_budget_s"])  # safety net only
    # the safety net must sit well above the time max_labels takes (calibrated at <= ~200 s)
    assert built.pareto_time_budget_s >= 3 * 200
    oracle_cfg["pareto_time_budget_s"] = None
    assert LabelSettingOracle.from_config(cfg, store=store).cfg.pareto_time_budget_s is None
    defaults = OracleConfig(energy=ENERGY)
    assert (defaults.max_labels, defaults.pareto_time_budget_s) == (40_000, 600.0)


# --------------------------------------------------------------------------- metrics


def _plan_result(item_id):
    return result(item_id, itins=[itinerary(stop("K", depart=80))])


def test_incomplete_item_is_a_finding_and_excluded_from_front_metrics():
    o = two_station_world(pareto_time_budget_s=0.0)
    m = planning_metrics([item("p")], [_plan_result("p")], o)
    assert m["oracle_search"]["n_items_searched"] == 1
    assert m["oracle_search"]["n_incomplete"] == 1 and m["oracle_search"]["items_incomplete"] == ["p"]
    assert m["oracle_search"]["incomplete_reasons"] == {"time_budget": 1}
    kinds = [f["kind"] for f in m["findings"]]
    assert kinds.count("oracle_search_incomplete") == 1
    assert kinds.count("oracle_nondeterministic_cutoff") == 1  # the wall-clock net fired, not max_labels
    assert "planner_plan_outside_oracle_front" not in kinds  # never judged against a partial front
    finding = next(f for f in m["findings"] if f["kind"] == "oracle_search_incomplete")
    assert finding["item_id"] == "p" and "wall-clock safety limit of 0 s hit" in finding["detail"]
    assert m["unknown_status"]["n_excluded_incomplete_search"] == 1 and m["unknown_status"]["n_oracle_plans"] == 0
    # Not front-based: feasibility and violations are unaffected by the budget.
    full = planning_metrics([item("p")], [_plan_result("p")], two_station_world())
    assert m["feasibility"] == full["feasibility"]
    assert full["oracle_search"]["n_incomplete"] == 0
    assert "oracle_search_incomplete" not in [f["kind"] for f in full["findings"]]


def test_unknown_power_sensitivity_skips_incomplete_searches():
    o = world([station("Q", 60, ports=[port(kw=None)])], pareto_time_budget_s=0.0)
    plan = itinerary(stop("Q", kw=None, depart=76))
    m = unknown_power_sensitivity([item("u1")], [result("u1", itins=[plan])], o)
    assert m["n_items"] == 1 and m["mean_delta_charge_min_25kw"] == pytest.approx(30.0)  # replay, not front-based
    assert m["n_excluded_incomplete_search"] == 1
    assert m["recommended_on_front_rate_25kw"] is None and m["oracle_fastest_change_rate_25kw"] is None


def test_front_metrics_only_for_items_with_a_planner_plan():
    o = two_station_world()
    m = planning_metrics([item("p"), item("q")], [_plan_result("p"), result("q", status="infeasible")], o)
    assert m["oracle_search"]["n_items_searched"] == 1  # q: no plan returned, no Pareto search
    assert unknown_status_reliance([item("q")], [result("q", status="infeasible")], o)["n_oracle_plans"] == 0


# --------------------------------------------------------------------------- deterministic limit


def test_max_labels_cutoff_is_deterministic():
    """The same input gives the same complete flag, label count and front, run after run, on fresh
    oracles (no shared cache) and with any wall-clock safety net that does not fire."""
    runs = []
    for budget in (None, 600.0, None):
        front = two_station_world(max_labels=3, pareto_time_budget_s=budget).pareto_plans(req(start=50))
        runs.append((front.complete, front.incomplete_reason, front.n_labels, front.plans))
    assert runs[0][:2] == (False, "max_labels")
    assert runs[0] == runs[1] == runs[2]
    full = [two_station_world().pareto_plans(req(start=50)) for _ in range(2)]
    assert full[0].complete and (full[0].n_labels, full[0].plans) == (full[1].n_labels, full[1].plans)


def test_max_labels_cutoff_is_not_a_nondeterministic_finding():
    m = planning_metrics([item("p")], [_plan_result("p")], two_station_world(max_labels=1))
    kinds = [f["kind"] for f in m["findings"]]
    assert kinds.count("oracle_search_incomplete") == 1
    assert "oracle_nondeterministic_cutoff" not in kinds
    assert m["oracle_search"]["items_nondeterministic_cutoff"] == []
