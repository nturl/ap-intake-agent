"""apintake CLI.

    apintake run [--mock] <case_file>   run one case through the pipeline
    apintake run [--mock] --all         run every case in data/inbox/
    apintake review                     interactively work the human review queue
    apintake trail <case_id>            pretty-print one case's full audit trail
    apintake eval [--mock]              run the eval harness against golden labels
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from apintake import approve, audit, ingest, pipeline

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _mock_enabled(args: argparse.Namespace) -> bool:
    if args.mock:
        return True
    if os.environ.get("APINTAKE_MOCK") == "1":
        return True
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "No ANTHROPIC_API_KEY set and --mock not passed. Real API calls require a key.\n"
            "Suggest: rerun with --mock (or set APINTAKE_MOCK=1)."
        )
        return True
    return False


def cmd_run(args: argparse.Namespace) -> int:
    mock = _mock_enabled(args)
    if args.all:
        cases = ingest.load_all_cases()
        if not cases:
            print(f"No cases found in {ingest.DEFAULT_INBOX_DIR}")
            return 1
        paths = [p for p, _ in cases]
    else:
        if not args.case_file:
            print("Provide a case file, or pass --all to run every case in data/inbox/.")
            return 1
        paths = [Path(args.case_file)]

    exit_code = 0
    for path in paths:
        try:
            outcome = pipeline.process_case(path, mock=mock)
        except Exception as e:  # noqa: BLE001 - surface any pipeline bug clearly per-case
            print(f"[{path.name}] ERROR: {e}")
            exit_code = 1
            continue
        print(f"[{outcome.case_id}] -> {outcome.outcome}")
        for reason in outcome.reasons:
            print(f"    - {reason}")
    return exit_code


def cmd_review(args: argparse.Namespace) -> int:
    approve.run_review()
    return 0


def cmd_trail(args: argparse.Namespace) -> int:
    events = audit.read_trail(case_id=args.case_id)
    print(audit.format_trail(events))
    return 0 if events else 1


def cmd_eval(args: argparse.Namespace) -> int:
    mock = True if (args.mock or not os.environ.get("ANTHROPIC_API_KEY")) else False
    sys.path.insert(0, str(PROJECT_ROOT / "evals"))
    import run_eval  # type: ignore

    return run_eval.main(mock=mock)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="apintake")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Run one case (or --all cases) through the pipeline")
    p_run.add_argument("case_file", nargs="?", help="Path to a case JSON file in data/inbox/")
    p_run.add_argument("--all", action="store_true", help="Run every case in data/inbox/")
    p_run.add_argument("--mock", action="store_true", help="Use fixture data instead of calling the API")
    p_run.set_defaults(func=cmd_run)

    p_review = sub.add_parser("review", help="Interactively work the human review queue")
    p_review.set_defaults(func=cmd_review)

    p_trail = sub.add_parser("trail", help="Pretty-print a case's audit trail")
    p_trail.add_argument("case_id", help="Case ID to show the trail for")
    p_trail.set_defaults(func=cmd_trail)

    p_eval = sub.add_parser("eval", help="Run the eval harness against data/golden/labels.json")
    p_eval.add_argument("--mock", action="store_true", help="Use fixture data instead of calling the API")
    p_eval.set_defaults(func=cmd_eval)

    return parser


def main(argv: list[str] | None = None) -> int:
    # Runtime ledger is untracked; bootstrap it from the tracked seed on first run.
    ledger = PROJECT_ROOT / "data" / "ledger.json"
    if not ledger.exists():
        shutil.copy(PROJECT_ROOT / "data" / "ledger.seed.json", ledger)
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
