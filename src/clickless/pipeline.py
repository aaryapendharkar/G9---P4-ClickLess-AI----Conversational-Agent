"""End-to-end online pipeline (proposal Fig. 1). Owner: the week's integration captain.

`build_pipeline(load_config("A"))` wires the implementations named in the config. Adding a new
implementation = add it to the registry below + reference it from a config. No module should
need to import another module's internals.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

from .config import repo_path
from .contracts import PlanResult, Route, TripRequest
from .data import DataStore
from .explain.grounded import GroundedLLMExplainer
from .explain.templates import TemplateExplainer
from .handoff.approval import ApprovalGate
from .handoff.maps_url import GoogleMapsHandoff
from .nlu.clarification import TemplateClarifier
from .nlu.llm_parser import LLMParser
from .nlu.rules_parser import RulesParser
from .nlu.spacy_parser import SpacyParser
from .planning.filters import DefaultHardConstraintFilter
from .planning.planner import ItineraryPlanner
from .planning.relaxation import MinCostRelaxer
from .planning.route_cache import OSRMRouteCache
from .planning.routing import (
    CachedRouter,
    CachingOSRMRouter,
    OSRMRouter,
    RouteNotCached,
)
from .planning.verifier import CompleteItineraryVerifier
from .ranking.logreg_ranker import LogRegRanker
from .ranking.tradeoffs import TradeoffSelector
from .ranking.weighted_ranker import WeightedRanker
from .retrieval.corridor import CorridorRetriever
from .retrieval.embedding_matcher import EmbeddingMatcher
from .retrieval.kg.enrich import KGEnricher
from .retrieval.tfidf_matcher import TfidfMatcher


def _build_router(
    config: dict[str, Any],
    store: DataStore,
) -> CachedRouter | CachingOSRMRouter:
    """Build the routing implementation selected by project configuration.

    Cached mode uses deterministic route fixtures. OSRM mode wraps the strict
    live client with local cache-first routing. The configuration stores an
    environment-variable name instead of embedding an OSRM URL in source code.
    """

    routing_config = config["routing"]
    routing_mode = routing_config["mode"]

    if routing_mode == "cache":
        return CachedRouter(
            store,
            config["energy"],
        )

    if routing_mode == "osrm":
        base_url_environment_variable = routing_config.get(
            "osrm_base_url_env"
        )

        if not base_url_environment_variable:
            raise ValueError(
                "OSRM routing requires the "
                "'routing.osrm_base_url_env' configuration field"
            )

        osrm_base_url = os.environ.get(
            base_url_environment_variable
        )

        if not osrm_base_url:
            raise ValueError(
                "OSRM routing requires environment variable "
                f"{base_url_environment_variable!r}"
            )

        live_osrm_router = OSRMRouter(
            base_url=osrm_base_url,
        )

        # Cache files are generated runtime data and are excluded by
        # .gitignore. Tests may override this location with a temporary
        # directory so they never write runtime data into the repository.
        cache_directory_setting = routing_config.get(
            "osrm_cache_dir",
            "data/cache/osrm",
        )
        cache_directory = repo_path(
            cache_directory_setting
        )

        return CachingOSRMRouter(
            live_router=live_osrm_router,
            route_cache=OSRMRouteCache(cache_directory),
        )

    raise ValueError(
        f"unsupported routing mode {routing_mode!r}; "
        "expected 'cache' or 'osrm'"
    )


class Pipeline:
    def __init__(self, cfg: dict[str, Any], store: DataStore) -> None:
        comp = cfg["components"]
        self.cfg, self.store = cfg, store
        self.name = cfg["name"]
        self.parser = {"rules": lambda: RulesParser(store), "spacy": lambda: SpacyParser(store),
                       "llm": lambda: LLMParser(store, cfg.get("llm", {}))}[comp["parser"]]()
        self.clarifier = TemplateClarifier() if comp["incomplete_request"] == "clarify" else None
        self.router = _build_router(
            cfg,
            store,
        )
        r = cfg["retrieval"]
        self.retriever = CorridorRetriever(store, r["corridor_buffer_km"], r["amenity_radius_m"], r["max_candidates"])
        self.enricher = KGEnricher(store, cfg.get("kg", {})) if comp["retriever"] == "corridor+kg" else None
        self.matcher = {"tfidf": lambda: TfidfMatcher(store),
                        "sbert": lambda: EmbeddingMatcher(store, r["sbert_model"])}[comp["matcher"]]()
        self.filter = DefaultHardConstraintFilter()
        self.planner = ItineraryPlanner(cfg["energy"])
        self.verifier = CompleteItineraryVerifier(store, cfg["energy"])
        self.relaxer = MinCostRelaxer(cfg["relaxation"])
        t = cfg["tradeoffs"]
        self.ranker = {"logreg": lambda: LogRegRanker(t), "weighted": lambda: WeightedRanker(t),
                       "weighted+evidence": lambda: WeightedRanker(t, use_evidence=True)}[comp["ranker"]]()
        self.selector = TradeoffSelector(t)
        self.explainer = (GroundedLLMExplainer(store, cfg.get("llm", {})) if comp["explainer"] == "grounded_llm"
                          else TemplateExplainer(store, comp.get("provenance_labels", False)))
        self.handoff = GoogleMapsHandoff(store, cfg["handoff"]["max_waypoints"])
        self.gate = ApprovalGate()
        self._routes: dict[str, Route] = {}

    # ------------------------------------------------------------------ plan
    def run(self, text: str, request_id: str | None = None) -> PlanResult:
        rid = request_id or uuid.uuid4().hex[:8]
        timings: dict[str, float] = {}
        t0 = time.perf_counter()

        def lap(name: str) -> None:
            nonlocal t0
            now = time.perf_counter()
            timings[name] = round((now - t0) * 1000, 2)
            t0 = now

        parse = self.parser.parse(text, rid)
        lap("parse")
        req = parse.request
        result = PlanResult(request_id=rid, config=self.name, status="ok", request=req, timings_ms=timings)

        if parse.needs_input:
            if self.clarifier:
                result.status, result.clarification = "needs_clarification", self.clarifier.question_for(parse)
            else:
                result.status = "rejected_incomplete"
                result.notes = parse.missing_fields + [f"ambiguous {k}" for k in parse.ambiguous_fields]
            return self._finish(result)

        return self.plan_request(req, result, lap)

    def plan_request(self, req: TripRequest, result: PlanResult, lap=lambda _n: None) -> PlanResult:
        vehicle = self.store.vehicle(req.vehicle_id)
        try:
            route = self.router.route(req.origin, req.destination)
        except RouteNotCached as e:
            result.status, result.notes = "error", [str(e)]
            return self._finish(result)
        self._routes[req.request_id] = route
        lap("route")

        cands = self.retriever.candidates(route, req)
        for c in cands:
            for na in c.amenities:
                for pref in req.amenities:
                    na.match_scores[pref.key] = self.matcher.score(pref, na.amenity)
            c.detour_min_est = self.router.detour_min(route, c)
        if self.enricher:
            cands = self.enricher.enrich(cands, req)
        lap("retrieve")

        kept, rejected = self.filter.apply(cands, req, vehicle)
        result.notes += [f"skipped {c.station.station_id}: {why}" for c, why in rejected]
        itineraries = self.planner.plan(req, vehicle, route, kept)
        verified = [
            itinerary
            for itinerary in itineraries
            if self.verifier.verify(itinerary, req, vehicle, route).ok
        ]
        lap("plan+verify")

        if not verified:
            result.status = "infeasible"
            try:
                result.relaxation = self.relaxer.find(req, lambda r: [])
            except NotImplementedError:
                result.notes.append("relaxation search not implemented yet")
            return self._finish(result)

        ranked = self.ranker.rank(verified, req)
        if getattr(self.ranker, "fallback_used", False):
            result.notes.append("logreg ranker not trained yet -- fell back to weighted cost")
        result.alternatives = self.selector.select(ranked, req)
        result.verification = self.verifier.verify(
            result.alternatives[0].itinerary,
            req,
            vehicle,
            route,
        )
        lap("rank")
        return self._finish(result)

    def _finish(self, result: PlanResult) -> PlanResult:
        result.explanation, result.claims = self.explainer.explain(result)
        return result

    # ------------------------------------------------------------------ handoff
    def approve(self, result: PlanResult, choice: int = 0) -> PlanResult:
        """Call ONLY after the user explicitly approves. Builds Maps URL(s) for the chosen option."""
        if result.status != "ok":
            raise ValueError("nothing to hand off")
        it = result.alternatives[choice].itinerary
        urls = self.handoff.build(it, self._routes[result.request_id])
        self.gate.record(result.request_id, approved=True, n_urls=len(urls))
        return result.model_copy(update={"handoff_urls": urls, "approved": True})


def build_pipeline(cfg: dict[str, Any], store: DataStore | None = None) -> Pipeline:
    return Pipeline(cfg, store or DataStore.from_config(cfg))
