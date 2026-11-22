"""Record which parser actually produced each parse: the configured LLM, or the rules fallback.

The B/C parser (`clickless.nlu.llm_parser.LLMParser`) falls back to the deterministic rules parser
when the endpoint times out or is unreachable, or when two responses fail validation. The
ParseResult still says `parser="llm"` and the reason only reaches a log warning, so a run whose
"LLM" items were really parsed by rules looks like a normal B/C run. The harness wraps the
pipeline's parser in ParserTrace and writes `parser_used` and `fallback_reason` on every row.

parser_used:
    "llm"             the LLM extraction was used
    "rules_fallback"  an LLM parser fell back to rules; fallback_reason says why
    otherwise         the configured non-LLM parser's name ("rules", "spacy"), never a fallback

Observation only: the parser's behaviour and its ParseResult are unchanged. If the parse result
itself starts reporting a fallback (a `fallback_reason` attribute), that is used first.

Owner: Ian.
"""

from __future__ import annotations

import logging
from typing import Any

LLM = "llm"
RULES_FALLBACK = "rules_fallback"
LLM_PARSER_LOGGER = "clickless.nlu.llm_parser"
NO_LOGGED_REASON = "fell back to rules without a logged reason"
_MAX_REASON = 500


class _Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _reason(messages: list[str]) -> str:
    text = "; ".join(m for m in messages if m) or NO_LOGGED_REASON
    return text if len(text) <= _MAX_REASON else text[: _MAX_REASON - 3] + "..."


class ParserTrace:
    """Wraps a parser; after each parse(), `last` holds {"parser_used", "fallback_reason"}."""

    def __init__(self, parser: Any) -> None:
        self.parser = parser
        self.last: dict[str, str | None] = self.empty()

    @staticmethod
    def empty() -> dict[str, str | None]:
        return {"parser_used": None, "fallback_reason": None}

    def __getattr__(self, name: str) -> Any:  # everything else behaves like the wrapped parser
        return getattr(self.parser, name)

    def parse(self, text: str, request_id: str):
        self.last = self.empty()
        name = getattr(self.parser, "name", type(self.parser).__name__)
        fallback = getattr(self.parser, "_fallback", None)
        if name != LLM or fallback is None:
            result = self.parser.parse(text, request_id)
            self.last = {"parser_used": name, "fallback_reason": None}
            return result

        fell_back = False

        def spy(*args: Any, **kwargs: Any):
            nonlocal fell_back
            fell_back = True
            return fallback(*args, **kwargs)

        own = vars(self.parser)
        had_own = "_fallback" in own
        saved = own.get("_fallback")
        collect = _Collect()
        logger = logging.getLogger(LLM_PARSER_LOGGER)
        logger.addHandler(collect)
        self.parser._fallback = spy
        try:
            result = self.parser.parse(text, request_id)
        finally:
            logger.removeHandler(collect)
            if had_own:
                self.parser._fallback = saved
            else:
                del self.parser._fallback

        reported = getattr(result, "fallback_reason", None)
        if reported:
            self.last = {"parser_used": RULES_FALLBACK, "fallback_reason": _reason([str(reported)])}
        elif fell_back:
            self.last = {"parser_used": RULES_FALLBACK, "fallback_reason": _reason(collect.messages)}
        else:
            self.last = {"parser_used": LLM, "fallback_reason": None}
        return result
