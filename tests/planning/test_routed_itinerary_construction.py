"""Tests for routed measurements used during itinerary construction."""

from unittest.mock import Mock

import pytest

import clickless.planning.planner as planner_module
import clickless.planning.route_points as route_points_module
from clickless.planning.planner import ItineraryPlanner
from clickless.planning.route_measurement import (
    GeometricRouteMeasurer,
    ItineraryRouteMeasurement,
    MeasuredLeg,
    TemporaryFailureFallbackMeasurer,
)
from clickless.planning.route_points import route_distance_scale
from clickless.planning.routing import OSRMTemporaryError


def test_planner_reuses_route_distance_scale_across_stop_sequences(
    pipe_a,
    cfg_a,
    demo_text,
    monkeypatch,
) -> None:
    """One route scale must serve every itinerary evaluated in a planning run."""

    request = pipe_a.parser.parse(
        demo_text,
        "route-scale-cache-test",
    ).request
    vehicle = pipe_a.store.vehicle(request.vehicle_id)
    route = pipe_a.router.route(
        request.origin,
        request.destination,
    )
    direct_route_measurement = ItineraryRouteMeasurement(
        points=(),
        legs=(
            MeasuredLeg(
                start_label=request.origin.text,
                end_label=request.destination.text,
                distance_km=50.0,
                duration_min=42.0,
            ),
        ),
        stop_detour_min=(),
    )
    route_measurer = Mock()
    route_measurer.measure.return_value = direct_route_measurement
    planner = ItineraryPlanner(
        cfg_a["energy"],
        route_measurer=route_measurer,
    )

    # Two sequences reproduce the important condition: final simulation runs
    # repeatedly while every sequence still uses the same unchanged route.
    monkeypatch.setattr(
        planner,
        "_build_stop_sequences",
        Mock(return_value=[[], []]),
    )

    planner_scale_calculation = Mock(wraps=route_distance_scale)
    unexpected_simulation_recalculation = Mock(
        side_effect=AssertionError(
            "simulation recalculated a route scale already supplied by the planner"
        )
    )
    monkeypatch.setattr(
        planner_module,
        "route_distance_scale",
        planner_scale_calculation,
    )
    monkeypatch.setattr(
        route_points_module,
        "route_distance_scale",
        unexpected_simulation_recalculation,
    )

    itineraries = planner.plan(
        request,
        vehicle,
        route,
        candidates=[],
    )

    assert len(itineraries) == 2
    assert planner_scale_calculation.call_count == 1
    unexpected_simulation_recalculation.assert_not_called()
    assert route_measurer.measure.call_count == 2


def test_planner_uses_injected_leg_distance_and_duration(
    pipe_a,
    cfg_a,
    demo_text,
) -> None:
    """Final simulation must use routed values rather than geometric formulas."""

    request = pipe_a.parser.parse(
        demo_text,
        "routed-leg-test",
    ).request
    vehicle = pipe_a.store.vehicle(request.vehicle_id)
    route = pipe_a.router.route(
        request.origin,
        request.destination,
    )
    routed_measurement = ItineraryRouteMeasurement(
        points=(),
        legs=(
            MeasuredLeg(
                start_label=request.origin.text,
                end_label=request.destination.text,
                distance_km=50.0,
                duration_min=42.0,
            ),
        ),
        stop_detour_min=(),
    )
    route_measurer = Mock()
    route_measurer.measure.return_value = routed_measurement
    planner = ItineraryPlanner(
        cfg_a["energy"],
        route_measurer=route_measurer,
    )

    itineraries = planner.plan(
        request,
        vehicle,
        route,
        candidates=[],
    )

    assert len(itineraries) == 1
    direct_itinerary = itineraries[0]
    assert direct_itinerary.legs[0].distance_km == pytest.approx(50.0)
    assert direct_itinerary.legs[0].drive_min == pytest.approx(42.0)
    route_measurer.measure.assert_called_once_with(route, [])


def test_simulation_rejects_measurement_with_wrong_leg_count(
    pipe_a,
    cfg_a,
    demo_text,
) -> None:
    """Incomplete routed data must not silently fall back to planner claims."""

    request = pipe_a.parser.parse(
        demo_text,
        "invalid-routed-leg-test",
    ).request
    vehicle = pipe_a.store.vehicle(request.vehicle_id)
    route = pipe_a.router.route(
        request.origin,
        request.destination,
    )
    route_measurer = Mock()
    route_measurer.measure.return_value = ItineraryRouteMeasurement(
        points=(),
        legs=(),
        stop_detour_min=(),
    )
    planner = ItineraryPlanner(
        cfg_a["energy"],
        route_measurer=route_measurer,
    )

    with pytest.raises(
        ValueError,
        match="one leg between every itinerary point",
    ):
        planner.plan(
            request,
            vehicle,
            route,
            candidates=[],
        )


def test_planner_collects_route_measurement_warning(
    pipe_a,
    cfg_a,
    demo_text,
) -> None:
    """A fallback warning must survive after the measured sequence is built."""

    request = pipe_a.parser.parse(
        demo_text,
        "routed-leg-warning-test",
    ).request
    vehicle = pipe_a.store.vehicle(request.vehicle_id)
    route = pipe_a.router.route(
        request.origin,
        request.destination,
    )
    unavailable_primary_measurer = Mock()
    unavailable_primary_measurer.measure.side_effect = OSRMTemporaryError(
        "test routed-leg outage"
    )
    fallback_policy = TemporaryFailureFallbackMeasurer(
        primary_measurer=unavailable_primary_measurer,
        fallback_measurer=GeometricRouteMeasurer(cfg_a["energy"]),
    )
    planner = ItineraryPlanner(
        cfg_a["energy"],
        route_measurer=fallback_policy,
    )

    planner.plan(
        request,
        vehicle,
        route,
        candidates=[],
    )

    assert fallback_policy.warning is not None
    assert planner.measurement_warnings == [fallback_policy.warning]
