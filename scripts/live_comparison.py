"""Runs the live pipeline against the real OpenAI API several times with the
demo scripted answers (examples/demo_answers.json), and summarises how often
each signal's linked IDs and evidence status match the answer key recorded
machine-readably in tests/fixtures/expected_results.json.

This is a manual diagnostic script, not a pytest test: run it by hand when
you want a sense of how much the investigation stage's output varies run to
run against a live model. It spends real API credits (one full pipeline run
per iteration) and requires a working .env (OPENAI_API_KEY, OPENAI_MODEL).

Usage:
    python scripts/live_comparison.py [--runs 5]

Each run writes its own runs/<run_id>/ directory as usual (git-ignored).
A machine-readable summary of every run is also written to
runs/live_comparison_summary.json; paste the printed summary table into
docs/test_results.md by hand — this script only measures, it does not edit
tests/fixtures/answer_key.md or any other fixture.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from cpc_agent import config  # noqa: E402
from cpc_agent.main import run_pipeline  # noqa: E402

EXPECTED = json.loads((config.BASE_DIR / "tests" / "fixtures" / "expected_results.json").read_text(encoding="utf-8"))
DEMO_ANSWERS = config.BASE_DIR / "examples" / "demo_answers.json"


def _evidence_records_by_measure_segment(run_dir: Path) -> dict[tuple[str, str], dict]:
    records = json.loads((run_dir / "evidence_records.json").read_text(encoding="utf-8"))
    return {(r["measure"], r["segment"]): r for r in records}


def run_once(index: int, total: int) -> dict:
    print(f"--- live run {index}/{total} ---", flush=True)
    runs_dir = config.RUNS_DIR
    before = {p.name for p in runs_dir.iterdir()} if runs_dir.exists() else set()

    result = run_pipeline(answers_path=str(DEMO_ANSWERS), data_dir=config.DATA_DIR, runs_dir=runs_dir)
    if result != 0:
        raise RuntimeError(f"live run {index} exited with status {result}")

    after = {p.name for p in runs_dir.iterdir()}
    new_dirs = after - before
    if len(new_dirs) != 1:
        raise RuntimeError(f"expected exactly one new run directory, got {sorted(new_dirs)}")
    run_dir = runs_dir / next(iter(new_dirs))

    by_key = _evidence_records_by_measure_segment(run_dir)
    signal_outcomes: dict[str, dict] = {}
    for signal_id, expected in EXPECTED["signals"].items():
        key = (expected["measure"], expected["segment"])
        record = by_key.get(key)
        if record is None:
            signal_outcomes[signal_id] = {"found": False}
            continue
        signal_outcomes[signal_id] = {
            "found": True,
            "aligned_ids": record["aligned_ids"],
            "countervailing_ids": record["countervailing_ids"],
            "evidence_status": record["evidence_status"],
            "aligned_exact_match": set(record["aligned_ids"]) == set(expected["aligned"]),
            "countervailing_exact_match": set(record["countervailing_ids"]) == set(expected["countervailing"]),
            "aligned_expected_recovered": set(expected["aligned"]) <= set(record["aligned_ids"]),
            "status_match": record["evidence_status"] == expected["status"],
        }
    return {"run_dir": run_dir.name, "signals": signal_outcomes}


def summarise(outcomes: list[dict]) -> str:
    n = len(outcomes)
    lines = [f"Live comparison over {n} run(s) against tests/fixtures/expected_results.json:", ""]
    for signal_id, expected in EXPECTED["signals"].items():
        found = sum(1 for o in outcomes if o["signals"][signal_id].get("found"))
        status_matches = sum(1 for o in outcomes if o["signals"][signal_id].get("status_match"))
        aligned_exact = sum(1 for o in outcomes if o["signals"][signal_id].get("aligned_exact_match"))
        aligned_recovered = sum(1 for o in outcomes if o["signals"][signal_id].get("aligned_expected_recovered"))
        countervailing_exact = sum(1 for o in outcomes if o["signals"][signal_id].get("countervailing_exact_match"))
        lines.append(
            f"{signal_id} ({expected['measure']}, {expected['segment']}): "
            f"found {found}/{n}; "
            f"evidence_status == answer key {status_matches}/{n}; "
            f"aligned_ids exact match {aligned_exact}/{n} "
            f"(expected aligned IDs fully recovered {aligned_recovered}/{n}); "
            f"countervailing_ids exact match {countervailing_exact}/{n}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runs", type=int, default=5, help="Number of live pipeline runs (default: 5).")
    args = parser.parse_args()

    outcomes = [run_once(i, args.runs) for i in range(1, args.runs + 1)]

    summary = summarise(outcomes)
    print()
    print(summary)

    report_path = config.RUNS_DIR / "live_comparison_summary.json"
    report_path.write_text(json.dumps(outcomes, indent=2), encoding="utf-8")
    print(f"\nPer-run detail written to {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
