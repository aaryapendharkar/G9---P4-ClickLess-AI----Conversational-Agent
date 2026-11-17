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


def test_clarification_options_are_visible_in_template_output(pipe_a, demo_text):
    from clickless.contracts import ClarificationQuestion

    result = pipe_a.run(demo_text, request_id="clarification-options")
    clarification_result = result.model_copy(
        update={
            "status": "needs_clarification",
            "clarification": ClarificationQuestion(
                field="vehicle_id",
                question="Which vehicle did you mean?",
                options=["Vehicle A", "Vehicle B"],
            ),
        }
    )

    explanation, claims = pipe_a.explainer.explain(clarification_result)

    assert explanation == "Which vehicle did you mean?\n1. Vehicle A\n2. Vehicle B"
    assert claims == []


def test_infeasible_connector_request_explains_vehicle_support(store):
    from clickless.contracts import Connector, PlanResult, TripRequest
    from clickless.explain.templates import TemplateExplainer

    request = TripRequest(
        request_id="connector-infeasible",
        raw_text="Use CHAdeMO in my Comet",
        vehicle_id="fixture-comet-awd-2026",
        connectors_override=[Connector.CHADEMO],
    )
    result = PlanResult(
        request_id=request.request_id,
        config="A",
        status="infeasible",
        request=request,
    )

    explanation, claims = TemplateExplainer(store).explain(result)

    assert "cannot use the requested connector(s): CHADEMO" in explanation
    assert "CCS1" in explanation and "NACS" in explanation
    assert claims == []


def test_grounded_explanation_cites_plan_evidence_when_present(pipe_a, store, demo_text):
    from clickless.contracts import Evidence

    result = pipe_a.run(demo_text, request_id="evidence-1")
    stop = result.alternatives[0].itinerary.stops[0]
    station = store.station(stop.station_id)
    evidence = [
        Evidence(
            evidence_id=f"kg:{stop.station_id}:name",
            subject_id=stop.station_id,
            field="name",
            value=station.name,
            provenance=station.provenance,
        ),
        Evidence(
            evidence_id=f"kg:{stop.station_id}:ports[0].connector",
            subject_id=stop.station_id,
            field="ports[0].connector",
            value=stop.connector.value,
            provenance=station.provenance,
        ),
        Evidence(
            evidence_id=f"kg:{stop.station_id}:ports[0].power_kw",
            subject_id=stop.station_id,
            field="ports[0].power_kw",
            value=stop.port_power_kw,
            provenance=station.provenance,
        ),
    ]
    grounded_result = result.model_copy(update={"evidence": evidence})

    _, claims = GroundedLLMExplainer(store, {}).explain(grounded_result)

    assert claims[0].supported_by[0] == "verified:evidence-1:stop:0"
    assert set(claims[0].supported_by[1:]) == {
        f"kg:{stop.station_id}:name",
        f"kg:{stop.station_id}:ports[0].connector",
        f"kg:{stop.station_id}:ports[0].power_kw",
    }


def test_kg_connector_and_power_citations_share_port_index(store):
    from clickless.contracts import Evidence
    from clickless.explain.grounded import GroundedLLMExplainer

    station = store.station("FIX-ST-004")
    evidence = [
        Evidence(
            evidence_id="kg:FIX-ST-004:ports[0].connector",
            subject_id="FIX-ST-004",
            field="ports[0].connector",
            value="CCS1",
            provenance=station.provenance,
        ),
        Evidence(
            evidence_id="kg:FIX-ST-004:ports[0].power_kw",
            subject_id="FIX-ST-004",
            field="ports[0].power_kw",
            value=50.0,
            provenance=station.provenance,
        ),
        Evidence(
            evidence_id="kg:FIX-ST-004:ports[1].connector",
            subject_id="FIX-ST-004",
            field="ports[1].connector",
            value="CCS1",
            provenance=station.provenance,
        ),
        Evidence(
            evidence_id="kg:FIX-ST-004:ports[1].power_kw",
            subject_id="FIX-ST-004",
            field="ports[1].power_kw",
            value=150.0,
            provenance=station.provenance,
        ),
    ]
    # Use a tiny result-shaped object only to exercise the pure port-fact selector.
    pipeline_result = type("EvidenceResult", (), {"evidence": evidence})()
    selected = GroundedLLMExplainer(store, {})._port_fact_ids(
        pipeline_result, "FIX-ST-004", "CCS1", 150.0
    )

    assert selected == [
        "kg:FIX-ST-004:ports[1].connector",
        "kg:FIX-ST-004:ports[1].power_kw",
    ]
