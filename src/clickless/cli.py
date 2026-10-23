"""Command line demo.

    python -m clickless plan "From Phoenix to Flagstaff in my Spark LR, 65% battery, lunch stop"
    python -m clickless plan --config C "..."          # once C's components exist
    python -m clickless plan --json "..."              # dump the full PlanResult
"""

from __future__ import annotations

import argparse
import sys

from .config import load_config
from .pipeline import build_pipeline


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="clickless")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan", help="plan a trip from one natural-language request")
    p.add_argument("text")
    p.add_argument("--config", default="A")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-input", action="store_true", help="never prompt for approval (scripts/tests)")
    args = ap.parse_args(argv)

    pipe = build_pipeline(load_config(args.config))
    result = pipe.run(args.text)
    print(result.model_dump_json(indent=2) if args.json else result.explanation)
    if result.notes and not args.json:
        print("\n[notes] " + "\n[notes] ".join(result.notes))

    if result.status == "ok" and not args.no_input and sys.stdin.isatty():
        choice = input("\nApprove handoff? Enter option number (1 = Recommended) or press Enter to skip: ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(result.alternatives):
            approved = pipe.approve(result, int(choice) - 1)
            print("\n".join(approved.handoff_urls))
    return 0
