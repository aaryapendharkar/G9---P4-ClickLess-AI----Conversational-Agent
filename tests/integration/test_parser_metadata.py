from clickless.nlu.rules_parser import RulesParser


class StubParser:
    name = "llm"

    def __init__(self, result):
        self.result = result

    def parse(self, text, request_id):
        updated_request = self.result.request.model_copy(update={"request_id": request_id})
        return self.result.model_copy(update={"request": updated_request})


def test_successful_parser_metadata_reaches_plan_result_and_json(pipe_a, demo_text):
    parsed = RulesParser(pipe_a.store).parse(demo_text, "metadata-ok")
    parsed = parsed.model_copy(update={"parser": "llm", "fallback_reason": None})
    pipe_a.parser = StubParser(parsed)

    result = pipe_a.run(demo_text, request_id="metadata-ok")
    restored = type(result).model_validate_json(result.model_dump_json())

    assert result.parser == "llm"
    assert result.fallback_reason is None
    assert restored.parser == "llm"
    assert restored.fallback_reason is None


def test_fallback_parser_metadata_reaches_plan_result_and_json(pipe_a, demo_text):
    parsed = RulesParser(pipe_a.store).parse(demo_text, "metadata-fallback")
    parsed = parsed.model_copy(
        update={"parser": "rules_fallback", "fallback_reason": "unavailable: timeout"}
    )
    pipe_a.parser = StubParser(parsed)

    result = pipe_a.run(demo_text, request_id="metadata-fallback")
    restored = type(result).model_validate_json(result.model_dump_json())

    assert result.parser == "rules_fallback"
    assert result.fallback_reason == "unavailable: timeout"
    assert restored.parser == "rules_fallback"
    assert restored.fallback_reason == "unavailable: timeout"
