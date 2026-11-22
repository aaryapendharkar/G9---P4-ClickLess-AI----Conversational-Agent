"""Command-line demo for planning and interactive clarification.

Examples:
    python -m clickless plan "From Phoenix to Flagstaff in my Spark LR, 65% battery, lunch stop"
    python -m clickless plan --config C "Phoenix to Flagstaff in my IONIQ 5"
    python -m clickless plan --json "From Phoenix to Flagstaff in my Spark LR, 65% battery"
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from .common.errors import NO_CACHED_ROUTE_NOTE
from .config import load_config
from .contracts import PlanResult
from .pipeline import build_pipeline

# Notes kept in PlanResult (and --json / saved eval results) for debugging, but not printed for
# the driver: the explanation already says what happened (#140), or sums them up in one
# "Not used:" line (#139, per-station "skipped <id>: <reason>" notes).
_DEBUG_ONLY_NOTE_PREFIXES = (NO_CACHED_ROUTE_NOTE, "skipped ")


def _quiet_model_loading(cfg: dict[str, Any]) -> None:
    """Keep Hugging Face startup noise off the demo screen (CLI only; tests and eval are unchanged).

    Configs B/C load a pinned Sentence-BERT model, which prints a "Loading weights" progress bar
    and, when it checks the Hub, an "unauthenticated requests" warning. Progress bars and library
    logging below errors are turned off; when the pinned revision is already in the local Hugging
    Face cache, the Hub is not contacted at all (HF_HUB_OFFLINE). Settings the user already
    exported win. Must run before huggingface_hub / transformers are imported.
    """
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("HF_HUB_VERBOSITY", "error")
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    retrieval = cfg.get("retrieval", {})
    model, revision = retrieval.get("sbert_model"), retrieval.get("sbert_revision")
    if model and revision and _hf_snapshot_cached(model, revision):
        os.environ.setdefault("HF_HUB_OFFLINE", "1")


def _hf_snapshot_cached(model: str, revision: str) -> bool:
    hub = (
        os.environ.get("HF_HUB_CACHE")
        or Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface") / "hub"
    )
    return (Path(hub) / f"models--{model.replace('/', '--')}" / "snapshots" / revision).is_dir()


def _display_result(
    result: PlanResult,
    *,
    as_json: bool,
) -> None:
    """Print one pipeline result, including any readable diagnostic notes."""

    print(result.model_dump_json(indent=2) if as_json else result.explanation)

    notes = [note for note in result.notes if not note.startswith(_DEBUG_ONLY_NOTE_PREFIXES)]
    if notes and not as_json:
        print("\n[notes] " + "\n[notes] ".join(notes))


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

    config = load_config(arguments.config)
    if argv is None:  # launched from the command line, not called from tests or other code
        _quiet_model_loading(config)
    pipeline = build_pipeline(config)
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
