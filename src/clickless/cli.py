"""Command-line demo for planning and interactive clarification.

Examples:
    python -m clickless plan "From Phoenix to Flagstaff in my Spark LR, 65% battery, lunch stop"
    python -m clickless plan --config C "Phoenix to Flagstaff in my IONIQ 5"
    python -m clickless plan --json "From Phoenix to Flagstaff in my Spark LR, 65% battery"
"""

from __future__ import annotations

import argparse
import sys

from .config import load_config
from .contracts import PlanResult
from .pipeline import build_pipeline


def _display_result(
    result: PlanResult,
    *,
    as_json: bool,
) -> None:
    """Print one pipeline result, including any readable diagnostic notes."""

    print(result.model_dump_json(indent=2) if as_json else result.explanation)

    if result.notes and not as_json:
        print("\n[notes] " + "\n[notes] ".join(result.notes))


def _read_optional_input(prompt: str) -> str | None:
    """Read one answer, treating closed input as a skipped prompt.

    On Windows, redirected input such as NUL can sometimes report itself as a
    terminal even though no answer can be read. In that case, input() raises
    EOFError. Returning None lets the CLI exit normally instead of crashing.
    """

    try:
        return input(prompt).strip()
    except EOFError:
        return None


def main(argv: list[str] | None = None) -> int:
    """Run one planning request and any interactive follow-up questions."""

    argument_parser = argparse.ArgumentParser(prog="clickless")
    subcommands = argument_parser.add_subparsers(
        dest="cmd",
        required=True,
    )

    plan_command = subcommands.add_parser(
        "plan",
        help="plan a trip from one natural-language request",
    )
    plan_command.add_argument("text")
    plan_command.add_argument("--config", default="A")
    plan_command.add_argument("--json", action="store_true")
    plan_command.add_argument(
        "--no-input",
        action="store_true",
        help="never prompt for clarification or approval (scripts/tests)",
    )
    arguments = argument_parser.parse_args(argv)

    pipeline = build_pipeline(load_config(arguments.config))
    result = pipeline.run(arguments.text)

    # A Config C request can require more than one answer. Display each
    # question, merge the driver's answer through the pipeline's approved
    # clarification interface, and repeat until planning reaches a final state.
    while True:
        _display_result(
            result,
            as_json=arguments.json,
        )

        if result.status != "needs_clarification":
            break

        # Automation and redirected-input runs must remain non-blocking. They
        # still receive the clarification question in the printed result.
        if arguments.no_input or not sys.stdin.isatty():
            return 0

        clarification_answer = _read_optional_input("\nAnswer clarification, or close input to skip: ")
        if clarification_answer is None:
            return 0

        result = pipeline.continue_clarification(
            result.request_id,
            clarification_answer,
        )

    # Approval remains a separate, explicit user action after a successful
    # plan. EOF is treated as skipping approval, matching --no-input behavior.
    if result.status == "ok" and not arguments.no_input and sys.stdin.isatty():
        approval_choice = _read_optional_input(
            "\nApprove handoff? Enter option number (1 = Recommended) or press Enter to skip: "
        )

        if approval_choice is None:
            return 0

        if approval_choice.isdigit() and 1 <= int(approval_choice) <= len(result.alternatives):
            approved_result = pipeline.approve(
                result,
                int(approval_choice) - 1,
            )
            print("\n".join(approved_result.handoff_urls))

    return 0
