"""Test minimum-cost request relaxation without using the planning oracle."""

from clickless.contracts import (
    Alternative,
    AmenityCategory,
    AmenityPreference,
    Itinerary,
    PlanCost,
    Relaxation,
    TripRequest,
)
from clickless.planning.relaxation import MinCostRelaxer


def _request_with_relaxable_fields() -> TripRequest:
    """Return a request containing each independently relaxable field."""

    return TripRequest(
        request_id="relaxation-test",
        raw_text="test relaxation request",
        start_soc_pct=60.0,
        max_detour_min=10.0,
        arrival_soc_pct_min=20.0,
        amenities=[
            AmenityPreference(
                category=AmenityCategory.COFFEE,
                phrase="coffee if convenient",
                priority="nice_to_have",
            ),
            AmenityPreference(
                category=AmenityCategory.FOOD,
                phrase="need food",
                priority="important",
            ),
        ],
    )


def _feasible_alternative() -> Alternative:
    """Return a minimal valid result produced by a fake solver."""

    itinerary = Itinerary(
        itinerary_id="relaxed-plan",
        request_id="relaxation-test",
        vehicle_id="test-vehicle",
        stops=[],
        legs=[],
        total_drive_min=100.0,
        total_charge_min=0.0,
        total_detour_min=0.0,
        arrival_soc_pct=20.0,
    )
    cost = PlanCost(
        profile="balanced",
        drive_min=100.0,
        charge_min=0.0,
        detour_penalty_min=0.0,
        stop_overhead_min=0.0,
        amenity_credit_min=0.0,
        evidence_credit_min=0.0,
        total_min=100.0,
        amenity_satisfaction=1.0,
    )

    return Alternative(
        label="Recommended",
        itinerary=itinerary,
        cost=cost,
    )


def _relaxer(cfg_a) -> MinCostRelaxer:
    """Build the relaxer using the repository configuration."""

    return MinCostRelaxer(cfg_a["relaxation"])


def test_relaxer_drops_nice_to_have_amenity_first(cfg_a):
    """The cheapest first step drops coffee, not important food."""

    request = _request_with_relaxable_fields()
    attempted_requests: list[TripRequest] = []

    def solve(candidate_request: TripRequest) -> list[Alternative]:
        attempted_requests.append(candidate_request)

        if len(candidate_request.amenities) == 1:
            return [_feasible_alternative()]

        return []

    relaxation = _relaxer(cfg_a).find(
        request,
        solve,
    )

    assert isinstance(relaxation, Relaxation)
    assert relaxation.penalty == 1.0
    assert relaxation.changes == ["drop amenity: coffee"]
    assert [preference.key for preference in attempted_requests[0].amenities] == [
        "food"
    ]


def test_relaxer_finds_combined_change_in_penalty_order(cfg_a):
    """A solution may require both an amenity and detour relaxation."""

    request = _request_with_relaxable_fields()

    def solve(candidate_request: TripRequest) -> list[Alternative]:
        coffee_was_dropped = [
            preference.key for preference in candidate_request.amenities
        ] == ["food"]
        detour_was_increased = candidate_request.max_detour_min == 15.0

        if coffee_was_dropped and detour_was_increased:
            return [_feasible_alternative()]

        return []

    relaxation = _relaxer(cfg_a).find(
        request,
        solve,
    )

    assert relaxation is not None
    assert relaxation.penalty == 4.0
    assert relaxation.changes == [
        "drop amenity: coffee",
        "max detour 10 -> 15 min",
    ]


def test_relaxer_never_lowers_arrival_charge_below_floor(cfg_a):
    """A non-multiple target stops exactly at the configured reserve floor."""

    request = TripRequest(
        request_id="arrival-floor-test",
        raw_text="arrive with at least 17 percent",
        arrival_soc_pct_min=17.0,
    )
    attempted_arrival_targets: list[float] = []

    def solve(candidate_request: TripRequest) -> list[Alternative]:
        attempted_arrival_targets.append(candidate_request.arrival_soc_pct_min)

        if candidate_request.arrival_soc_pct_min == 10.0:
            return [_feasible_alternative()]

        return []

    relaxation = _relaxer(cfg_a).find(
        request,
        solve,
    )

    assert relaxation is not None
    assert relaxation.changes == ["arrival charge 17% -> 10%"]
    assert min(attempted_arrival_targets) == 10.0


def test_relaxer_preserves_non_relaxable_request_fields(cfg_a):
    """Safety and vehicle fields survive every copied candidate request."""

    request = _request_with_relaxable_fields().model_copy(
        update={
            "vehicle_id": "test-vehicle",
            "start_soc_pct": 60.0,
            "min_power_kw": 150.0,
        }
    )

    def solve(candidate_request: TripRequest) -> list[Alternative]:
        assert candidate_request.vehicle_id == request.vehicle_id
        assert candidate_request.start_soc_pct == request.start_soc_pct
        assert candidate_request.min_power_kw == request.min_power_kw

        if candidate_request.max_detour_min == 15.0:
            return [_feasible_alternative()]

        return []

    relaxation = _relaxer(cfg_a).find(
        request,
        solve,
    )

    assert relaxation is not None


def test_relaxer_returns_none_when_no_permitted_change_works(cfg_a):
    """Exhausting the finite search returns None rather than looping."""

    request = _request_with_relaxable_fields()

    def never_feasible(
        _candidate_request: TripRequest,
    ) -> list[Alternative]:
        return []

    relaxation = _relaxer(cfg_a).find(
        request,
        never_feasible,
    )

    assert relaxation is None
