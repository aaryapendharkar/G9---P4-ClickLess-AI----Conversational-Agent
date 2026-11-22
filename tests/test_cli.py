"""Tests for interactive clarification and approval in the command-line demo."""

from __future__ import annotations

from types import SimpleNamespace

import clickless.cli as cli


class FakeResult:
    """Small PlanResult stand-in containing only fields used by the CLI."""

    def __init__(
        self,
        status: str,
        explanation: str,
        *,
        request_id: str = "cli-request",
        alternatives: list[object] | None = None,
        handoff_urls: list[str] | None = None,
    ) -> None:
        self.request_id = request_id
        self.status = status
        self.explanation = explanation
        self.notes: list[str] = []
        self.alternatives = alternatives or []
        self.handoff_urls = handoff_urls or []

    def model_dump_json(self, indent: int = 2) -> str:
        """Support the CLI's existing --json output path."""

        return f'{{\n{" " * indent}"request_id": "{self.request_id}",\n{" " * indent}"status": "{self.status}"\n}}'


class FakePipeline:
    """Record how the CLI drives the pipeline across multiple user answers."""

    def __init__(
        self,
        initial_result: FakeResult,
        continuation_results: list[FakeResult] | None = None,
    ) -> None:
        self.initial_result = initial_result
        self.continuation_results = iter(continuation_results or [])
        self.clarification_answers: list[tuple[str, str]] = []
        self.approved_option_index: int | None = None

    def run(self, text: str) -> FakeResult:
        self.original_text = text
        return self.initial_result

    def continue_clarification(
        self,
        request_id: str,
        answer: str,
    ) -> FakeResult:
        self.clarification_answers.append((request_id, answer))
        return next(self.continuation_results)

    def approve(
        self,
        result: FakeResult,
        option_index: int,
    ) -> FakeResult:
        self.approved_option_index = option_index
        return FakeResult(
            "ok",
            result.explanation,
            request_id=result.request_id,
            alternatives=result.alternatives,
            handoff_urls=["https://maps.example/approved-route"],
        )


def _configure_cli(
    monkeypatch,
    pipeline: FakePipeline,
    *,
    interactive: bool,
) -> None:
    """Replace configuration, pipeline construction, and terminal detection."""

    monkeypatch.setattr(cli, "load_config", lambda _name: {})
    monkeypatch.setattr(cli, "build_pipeline", lambda _config: pipeline)
    monkeypatch.setattr(
        cli.sys,
        "stdin",
        SimpleNamespace(isatty=lambda: interactive),
    )


def test_interactive_clarification_continues_to_result_and_approval(
    monkeypatch,
    capsys,
):
    """One answer should resume planning and preserve the approval flow."""

    initial = FakeResult(
        "needs_clarification",
        "What percentage battery do you have right now?",
    )
    completed = FakeResult(
        "ok",
        "A verified route is ready.",
        alternatives=[object()],
    )
    pipeline = FakePipeline(initial, [completed])
    _configure_cli(monkeypatch, pipeline, interactive=True)

    scripted_answers = iter(["65%", "1"])
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: next(scripted_answers),
    )

    exit_code = cli.main(
        [
            "plan",
            "--config",
            "C",
            "Phoenix to Flagstaff in my IONIQ 5",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert pipeline.clarification_answers == [("cli-request", "65%")]
    assert pipeline.approved_option_index == 0
    assert "What percentage battery" in output
    assert "A verified route is ready." in output
    assert "https://maps.example/approved-route" in output


def test_multiple_clarification_questions_repeat_until_complete(
    monkeypatch,
    capsys,
):
    """The CLI must continue asking until the pipeline leaves clarification."""

    initial = FakeResult(
        "needs_clarification",
        "Which vehicle did you mean?",
    )
    second_question = FakeResult(
        "needs_clarification",
        "What percentage battery do you have right now?",
    )
    completed = FakeResult(
        "ok",
        "Planning completed after two answers.",
        alternatives=[object()],
    )
    pipeline = FakePipeline(
        initial,
        [second_question, completed],
    )
    _configure_cli(monkeypatch, pipeline, interactive=True)

    scripted_answers = iter(["IONIQ 5 Long Range", "65%", ""])
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: next(scripted_answers),
    )

    exit_code = cli.main(
        [
            "plan",
            "--config",
            "C",
            "Phoenix to Flagstaff",
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert pipeline.clarification_answers == [
        ("cli-request", "IONIQ 5 Long Range"),
        ("cli-request", "65%"),
    ]
    assert "Which vehicle" in output
    assert "What percentage battery" in output
    assert "Planning completed after two answers." in output


def test_no_input_prints_question_without_continuing(
    monkeypatch,
    capsys,
):
    """Automation mode must never wait for a clarification answer."""

    initial = FakeResult(
        "needs_clarification",
        "What percentage battery do you have right now?",
    )
    pipeline = FakePipeline(initial)
    _configure_cli(monkeypatch, pipeline, interactive=True)
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: (_ for _ in ()).throw(AssertionError("input must not be called with --no-input")),
    )

    exit_code = cli.main(
        [
            "plan",
            "--config",
            "C",
            "--no-input",
            "Phoenix to Flagstaff in my IONIQ 5",
        ]
    )

    assert exit_code == 0
    assert pipeline.clarification_answers == []
    assert "What percentage battery" in capsys.readouterr().out


def test_noninteractive_stdin_does_not_prompt(
    monkeypatch,
    capsys,
):
    """Piped commands should print the question and exit without blocking."""

    initial = FakeResult(
        "needs_clarification",
        "What percentage battery do you have right now?",
    )
    pipeline = FakePipeline(initial)
    _configure_cli(monkeypatch, pipeline, interactive=False)
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: (_ for _ in ()).throw(AssertionError("input must not be called without a terminal")),
    )

    exit_code = cli.main(
        [
            "plan",
            "--config",
            "C",
            "Phoenix to Flagstaff in my IONIQ 5",
        ]
    )

    assert exit_code == 0
    assert pipeline.clarification_answers == []
    assert "What percentage battery" in capsys.readouterr().out


def test_eof_during_clarification_exits_cleanly(
    monkeypatch,
):
    """A terminal that immediately closes should behave like skipped input."""

    initial = FakeResult(
        "needs_clarification",
        "What percentage battery do you have right now?",
    )
    pipeline = FakePipeline(initial)
    _configure_cli(monkeypatch, pipeline, interactive=True)
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: (_ for _ in ()).throw(EOFError),
    )

    assert cli.main(["plan", "--config", "C", "Phoenix to Flagstaff"]) == 0
    assert pipeline.clarification_answers == []


def test_eof_during_approval_exits_cleanly(
    monkeypatch,
):
    """Windows NUL input must not crash at the approval prompt."""

    completed = FakeResult(
        "ok",
        "A verified route is ready.",
        alternatives=[object()],
    )
    pipeline = FakePipeline(completed)
    _configure_cli(monkeypatch, pipeline, interactive=True)
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: (_ for _ in ()).throw(EOFError),
    )

    assert cli.main(["plan", "Phoenix to Flagstaff"]) == 0
    assert pipeline.approved_option_index is None
