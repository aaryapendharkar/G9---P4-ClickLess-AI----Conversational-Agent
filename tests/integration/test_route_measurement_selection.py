"""Test how pipeline routing mode selects verifier measurements."""

from copy import deepcopy

from clickless.pipeline import build_pipeline
from clickless.planning.route_measurement import (
    GeometricRouteMeasurer,
    OSRMRouteMeasurer,
)
from clickless.planning.routing import CachingOSRMRouter


def test_cached_fixture_mode_keeps_geometric_verifier_measurements(pipe_a):
    """Configuration A remains deterministic and requires no network data."""

    assert isinstance(
        pipe_a.verifier.route_measurer,
        GeometricRouteMeasurer,
    )


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
        pipeline.verifier.route_measurer,
        OSRMRouteMeasurer,
    )
    assert pipeline.verifier.route_measurer.route_provider is pipeline.router
