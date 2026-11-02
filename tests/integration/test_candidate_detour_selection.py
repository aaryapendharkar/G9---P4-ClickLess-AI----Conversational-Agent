"""Test how pipeline routing mode selects candidate-detour measurements."""

from copy import deepcopy
from unittest.mock import Mock

from clickless.pipeline import build_pipeline
from clickless.planning.routing import (
    CachingOSRMRouter,
    OSRMTemporaryError,
    TemporaryFailureDetourMeasurer,
)


def test_cache_mode_uses_its_router_for_candidate_detours(pipe_a) -> None:
    """Configuration A keeps its existing deterministic detour behavior."""

    assert pipe_a.detour_measurer is pipe_a.router


def test_osrm_mode_constructs_candidate_detour_fallback_policy(
    cfg_a,
    store,
    tmp_path,
    monkeypatch,
) -> None:
    """OSRM mode combines cache-first routing with geometric fallback."""

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
        pipeline.detour_measurer,
        TemporaryFailureDetourMeasurer,
    )
    assert pipeline.detour_measurer.primary_measurer is pipeline.router


def test_candidate_detour_fallback_warning_is_returned(
    pipe_a,
    demo_text,
) -> None:
    """The plan notes must disclose any estimated candidate detours."""

    unavailable_osrm_measurer = Mock()
    unavailable_osrm_measurer.detour_min.side_effect = OSRMTemporaryError(
        "test candidate-detour outage"
    )
    fallback_policy = TemporaryFailureDetourMeasurer(
        primary_measurer=unavailable_osrm_measurer,
        fallback_measurer=pipe_a.router,
    )
    pipe_a.detour_measurer = fallback_policy

    result = pipe_a.run(
        demo_text,
        request_id="candidate-detour-warning-test",
    )

    assert result.status == "ok"
    assert fallback_policy.warning is not None
    assert result.notes.count(fallback_policy.warning) == 1
