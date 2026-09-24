"""CLI entry point: python -m cpc_agent.main [--answers answers.json].

The full pipeline (validation -> metrics -> screening -> investigation ->
gates -> brief -> audit log) is wired up in later build phases. This
skeleton exists so the package is runnable from phase 1 onward.
"""
from __future__ import annotations

import argparse
import sys


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m cpc_agent.main",
        description="CPC Preparation Agent — prepares a draft workshop brief for human review.",
    )
    parser.add_argument(
        "--answers",
        type=str,
        default=None,
        help="Path to a scripted-mode answers JSON file (see examples/demo_answers.json).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    print("CPC Preparation Agent: pipeline not yet built (see CLAUDE.md build phases).")
    print(f"answers file: {args.answers or '(interactive mode)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
