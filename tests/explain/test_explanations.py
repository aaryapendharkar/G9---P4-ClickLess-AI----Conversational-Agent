from clickless.explain.grounded import GroundedLLMExplainer


def test_template_explanation_names_skipped_stations_and_follow_up(pipe_a, demo_text):
    result = pipe_a.run(demo_text, request_id="explain-1")

    assert result.status == "ok"
    assert "Not used:" in result.explanation
    assert "Say 'prioritize food'" in result.explanation


def test_grounded_explanation_claims_only_reference_verified_request(pipe_a, store, demo_text):
    result = pipe_a.run(demo_text, request_id="explain-2")
    explanation, claims = GroundedLLMExplainer(store, {}).explain(result)

    assert "Source:" in explanation
    assert result.alternatives[0].itinerary.stops[0].station_name in explanation
    assert claims
    assert all(claim.supported_by for claim in claims)
    assert all(claim.supported_by[0].startswith("verified:explain-2:") for claim in claims)
