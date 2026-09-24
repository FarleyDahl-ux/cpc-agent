"""CLI entry point: python -m cpc_agent.main [--answers answers.json].

Wires together every pipeline stage in the order set out in CLAUDE.md §4:
scope gate -> validate -> calculate/flag -> screen comments -> investigate
(LLM) -> evidence status -> findings review gate -> brief -> final approval
gate. HALT stops the run immediately; PAUSE asks the analyst (interactively
or via the scripted answers file) and records the decision; LIMITATION is
recorded and the run continues.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import audit, config
from .brief import populate_cpc_template, render_brief_html, update_brief_status
from .evidence import build_evidence_record
from .gates import Gates
from .investigate import run_investigation
from .loaders import load_approved_sources
from .metrics import calculate_metrics, flag_signals
from .screening import quarantine_summary, screen_comments
from .validation import (
    HaltError,
    blank_value_summary,
    check_reporting_context,
    check_satisfaction_sample_size,
    find_blank_required_values,
    low_sample_excluded_summary,
    low_sample_proceeded_summary,
    missing_optional_files,
    reconcile_status_counts,
    reconciliation_summary,
    segment_of,
    validate_schema,
)


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


def _filter_excluded(rows: list[dict], filename: str, excluded: set[tuple]) -> list[dict]:
    return [row for row in rows if tuple(segment_of(filename, row).values()) not in excluded]


def run_pipeline(
    answers_path: str | None = None,
    data_dir: Path = config.DATA_DIR,
    runs_dir: Path = config.RUNS_DIR,
    client=None,
    model: str | None = None,
) -> int:
    run_id = audit.new_run_id()
    log = audit.AuditLog(run_id, runs_dir=runs_dir)
    log.log_event("run_start", instruction_version=config.INSTRUCTION_VERSION, scripted=answers_path is not None)

    sources = load_approved_sources(data_dir)
    log.log_event("input_files", hashes=audit.input_file_hashes(sources))
    data_version = audit.compute_data_version(sources)

    gates = Gates(answers_path, audit=log)

    context = check_reporting_context(sources)
    if not gates.confirm_scope(context):
        log.log_event("run_end", status="aborted_at_scope_gate")
        log.close()
        print("Run aborted at the scope gate.")
        return 1

    try:
        validate_schema(sources)
    except HaltError as exc:
        log.log_event("halt", reason=str(exc))
        log.log_event("run_end", status="halted")
        log.close()
        print(f"HALT: {exc}")
        return 1

    exceptions: dict[str, list[dict]] = {"pauses": [], "exclusions": [], "quarantined": [], "limitations": []}

    for key in missing_optional_files(sources):
        filename = config.OPTIONAL_FILES[key]
        detail = f"{filename} not available; continuing with quantitative findings only."
        summary = (
            "Learner comments weren't available for this run, so only the numeric results are included."
            if key == "student_feedback"
            else f"{filename} wasn't available for this run."
        )
        exceptions["limitations"].append({"file": filename, "detail": detail, "summary": summary})
        log.log_event("limitation", file=filename, detail=detail, summary=summary)

    excluded: dict[str, set[tuple]] = {
        "course_metrics.csv": set(),
        "satisfaction.csv": set(),
        "unit_results.csv": set(),
    }

    def _record_exclusion(issue_key: str, segment: dict[str, str], filename: str, reason: str, summary: str) -> None:
        detail = f"Excluded {', '.join(f'{k}={v}' for k, v in segment.items())} from {filename} — {reason} (value never estimated)."
        exceptions["exclusions"].append({"issue_key": issue_key, "detail": detail, "summary": summary})
        log.log_event("exclusion", issue_key=issue_key, detail=detail, summary=summary)

    for issue in find_blank_required_values(sources):
        decision = gates.resolve_blank_value(issue)
        if decision == "abort":
            log.log_event("halt", reason=f"Analyst aborted on blank value: {issue.issue_key}")
            log.log_event("run_end", status="halted")
            log.close()
            print(f"HALT: analyst aborted on blank value for {issue.issue_key}")
            return 1
        excluded[issue.file].add(tuple(issue.segment.values()))
        _record_exclusion(issue.issue_key, issue.segment, issue.file, f"blank '{issue.field}'", blank_value_summary(issue))

    for issue in reconcile_status_counts(sources.get("course_metrics").rows):
        decision = gates.resolve_reconciliation(issue)
        if decision == "abort":
            log.log_event("halt", reason=f"Analyst aborted on reconciliation mismatch: {issue.issue_key}")
            log.log_event("run_end", status="halted")
            log.close()
            print(f"HALT: analyst aborted on reconciliation mismatch for {issue.issue_key}")
            return 1
        excluded["course_metrics.csv"].add(tuple(issue.segment.values()))
        _record_exclusion(issue.issue_key, issue.segment, "course_metrics.csv", "failed reconciliation", reconciliation_summary(issue))

    for issue in check_satisfaction_sample_size(sources.get("satisfaction").rows):
        decision = gates.resolve_low_sample(issue)
        if decision == "exclude":
            excluded["satisfaction.csv"].add(tuple(issue.segment.values()))
            _record_exclusion(
                issue.issue_key, issue.segment, "satisfaction.csv", "n_respondents below minimum sample size",
                low_sample_excluded_summary(issue),
            )
        else:
            detail = f"Proceeded with {issue.segment} despite n_respondents={issue.n_respondents:.0f} below minimum sample size."
            summary = low_sample_proceeded_summary(issue)
            exceptions["limitations"].append({"issue_key": issue.issue_key, "detail": detail, "summary": summary})
            log.log_event("low_sample_proceeded", issue_key=issue.issue_key, detail=detail, summary=summary)

    course_metrics_rows = _filter_excluded(sources.get("course_metrics").rows, "course_metrics.csv", excluded["course_metrics.csv"])
    satisfaction_rows = _filter_excluded(sources.get("satisfaction").rows, "satisfaction.csv", excluded["satisfaction.csv"])
    unit_rows = _filter_excluded(sources.get("unit_results").rows, "unit_results.csv", excluded["unit_results.csv"])

    bundle = calculate_metrics(course_metrics_rows, satisfaction_rows, unit_rows)
    signals = flag_signals(bundle)
    log.log_event(
        "signals_flagged",
        signals=[{"id": s.id, "measure": s.measure, "segment": s.segment, "value": s.value, "n": s.n} for s in signals],
    )

    feedback_rows = sources.get("student_feedback").rows if sources.get("student_feedback").present else []
    screening_result = screen_comments(feedback_rows)
    for quarantined in screening_result.quarantined:
        detail = f"Quarantined comment {quarantined.comment_id}: {quarantined.reason}."
        summary = quarantine_summary(quarantined.reasons)
        exceptions["quarantined"].append(
            {"comment_id": quarantined.comment_id, "reason": quarantined.reason, "detail": detail, "summary": summary}
        )
        log.log_event("quarantine", comment_id=quarantined.comment_id, reason=quarantined.reason, summary=summary)

    investigation = run_investigation(
        signals,
        screening_result.eligible,
        ask_analyst_fn=gates.ask_analyst,
        audit_log_tool_call_fn=log.log_tool_call,
        client=client,
        model=model,
    )
    if signals and screening_result.eligible and not investigation.finished:
        detail = "Investigation loop ended without an explicit finish_investigation call."
        summary = "The evidence-gathering step ended before every finding could be reviewed."
        exceptions["limitations"].append({"detail": detail, "summary": summary})
        log.log_event("limitation", detail=detail, summary=summary)

    evidence_records = []
    for signal in signals:
        record = build_evidence_record(
            signal,
            aligned_ids=investigation.aligned_ids(signal.id),
            countervailing_ids=investigation.countervailing_ids(signal.id),
            themes=investigation.themes(signal.id),
        )
        evidence_records.append(record)
        log.log_event("evidence_status", finding_id=signal.id, status=record.evidence_status)

    comments_by_id = {c.comment_id: c for c in screening_result.eligible}
    reviewed = gates.review_findings(evidence_records, comments_by_id)
    accepted = [r for r in reviewed if r.reviewer_status in ("accept", "edit")]

    generated_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    snapshot_date = next(
        (row["snapshot_date"] for row in sources.get("course_metrics").rows if row.get("reporting_period") == config.CURRENT_PERIOD),
        "n/a",
    )
    brief_path = populate_cpc_template(
        accepted,
        context,
        run_id,
        data_version,
        config.INSTRUCTION_VERSION,
        generated_date,
        snapshot_date,
        exceptions,
        log.run_dir,
        analyst_name=gates.analyst_name,
    )
    log.log_event("brief_written", path=str(brief_path))

    decision = gates.final_approval(brief_path)
    update_brief_status(brief_path, decision)

    html_path = render_brief_html(
        accepted,
        context,
        run_id,
        data_version,
        generated_date,
        snapshot_date,
        exceptions,
        gates.analyst_name,
        decision,
        comments_by_id,
        log.run_dir,
    )
    log.log_event("brief_html_written", path=str(html_path))

    (log.run_dir / "evidence_records.json").write_text(
        json.dumps([r.to_dict() for r in reviewed], indent=2), encoding="utf-8"
    )
    (log.run_dir / "exception_log.json").write_text(json.dumps(exceptions, indent=2), encoding="utf-8")

    log.log_event("run_end", status="completed", final_decision=decision)
    log.close()

    print(f"Run {run_id} complete. Outputs written to {log.run_dir}")
    print(f"Final decision: {decision}. The brief is not sent, emailed or published.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return run_pipeline(answers_path=args.answers)


if __name__ == "__main__":
    sys.exit(main())
