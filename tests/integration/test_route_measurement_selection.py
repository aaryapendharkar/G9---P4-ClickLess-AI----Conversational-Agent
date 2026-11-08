"""Test how pipeline routing mode selects verifier measurements."""

from copy import deepcopy
from unittest.mock import Mock

from clickless.pipeline import build_pipeline
from clickless.planning.route_measurement import (
    GeometricRouteMeasurer,
    OSRMRouteMeasurer,
    TemporaryFailureFallbackMeasurer,
)
from clickless.planning.routing import CachingOSRMRouter, OSRMTemporaryError


def test_cached_fixture_mode_keeps_geometric_verifier_measurements(pipe_a):
    """Configuration A remains deterministic and requires no network data."""

    assert isinstance(
        pipe_a.planner.route_measurer,
        GeometricRouteMeasurer,
    )
    assert isinstance(
        pipe_a.verifier.route_measurer,
        GeometricRouteMeasurer,
    )
    assert pipe_a.planner.route_measurer is not pipe_a.verifier.route_measurer


def test_osrm_mode_injects_cache_first_verifier_measurements(
    cfg_a,
    store,
    tmp_path,
    monkeypatch,
):
    """OSRM mode gives the verifier the same cache-first routing provider."""

    osrm_environment_variable = "CLICKLESS_TEST_OSRM_BASE_URL"
    monkeypatch.setenv(
        osrm_environment_variable,
        "https://router.test.invalid",
    )
    osrm_config = deepcopy(cfg_a)
    osrm_config["routing"] = {
        "mode": "osrm",
        "osrm_base_url_env": osrm_environment_variable,
        "osrm_cache_dir": str(tmp_path / "osrm"),
    }

    pipeline = build_pipeline(
        osrm_config,
        store,
    )

    assert isinstance(pipeline.router, CachingOSRMRouter)
    assert isinstance(
        pipeline.planner.route_measurer,
        TemporaryFailureFallbackMeasurer,
    )
    assert isinstance(
        pipeline.verifier.route_measurer,
        TemporaryFailureFallbackMeasurer,
    )
    assert isinstance(
        pipeline.verifier.route_measurer.primary_measurer,
        OSRMRouteMeasurer,
    )
    assert (
        pipeline.verifier.route_measurer.primary_measurer.route_provider
        is pipeline.router
    )
    assert isinstance(
        pipeline.verifier.route_measurer.fallback_measurer,
        GeometricRouteMeasurer,
    )
    assert pipeline.planner.route_measurer is not pipeline.verifier.route_measurer


def test_final_verification_adds_geometric_fallback_warning(
    pipe_a,
    cfg_a,
    demo_text,
):
    """The returned plan must disclose estimated verifier measurements."""

    unavailable_osrm_measurer = Mock()
    unavailable_osrm_measurer.measure.side_effect = OSRMTemporaryError(
        "test OSRM outage"
    )
    fallback_policy = TemporaryFailureFallbackMeasurer(
        primary_measurer=unavailable_osrm_measurer,
        fallback_measurer=GeometricRouteMeasurer(cfg_a["energy"]),
    )
    pipe_a.verifier.route_measurer = fallback_policy

    result = pipe_a.run(
        demo_text,
        request_id="fallback-warning-test",
    )

    assert result.status == "ok"
    assert result.verification is not None
    assert result.verification.ok
    assert fallback_policy.warning is not None
    assert result.notes.count(fallback_policy.warning) == 1


def test_successful_final_verification_adds_no_fallback_warning(
    pipe_a,
    demo_text,
):
    """Normal deterministic verification must not report an OSRM outage."""

    result = pipe_a.run(
        demo_text,
        request_id="no-fallback-warning-test",
    )

    assert result.status == "ok"
    assert not any("OSRM measurement unavailable" in note for note in result.notes)


def test_planner_route_measurement_warning_is_returned(
    pipe_a,
    cfg_a,
    demo_text,
):
    """Plan notes must disclose routed-leg estimates used during simulation."""

    unavailable_osrm_measurer = Mock()
    unavailable_osrm_measurer.measure.side_effect = OSRMTemporaryError(
        "test planner route-measurement outage"
    )
    fallback_policy = TemporaryFailureFallbackMeasurer(
        primary_measurer=unavailable_osrm_measurer,
        fallback_measurer=GeometricRouteMeasurer(cfg_a["energy"]),
    )
    pipe_a.planner.route_measurer = fallback_policy

    result = pipe_a.run(
        demo_text,
        request_id="planner-route-warning-test",
    )

    assert result.status == "ok"
    assert fallback_policy.warning is not None
    assert result.notes.count(fallback_policy.warning) == 1
