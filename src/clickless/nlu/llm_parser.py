"""Configurations B/C parser: LLM -> schema-validated TripRequest (proposal eq. 2). Owner: Deepti.

Plan:
  1. prompt = system instructions + JSON schema of the extraction fields + few-shot DEV examples
     (never test items) -> prompts/extract_v1.txt, versioned in git
  2. call voyager_client.chat(..., temperature=0) asking for JSON only
  3. validate with pydantic; on failure retry once with the validation error, then give up
  4. resolve places with DataStore.geocode and vehicles with DataStore.find_vehicles --
     the LLM proposes strings, our data decides ids (no invented vehicle ids)
  5. any safety-critical field the LLM could not ground -> missing_fields (never a default)
Log raw LLM outputs to results/llm_logs/ for error analysis.
"""

from __future__ import annotations

from ..contracts import ParseResult


class LLMParser:
    name = "llm"

    def __init__(self, store, llm_cfg: dict) -> None:
        self.store = store
        self.llm_cfg = llm_cfg

    def parse(self, text: str, request_id: str) -> ParseResult:
        raise NotImplementedError("TODO(Deepti): LLM extraction via Voyager (Config B/C)")
