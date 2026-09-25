"""CLI entry point: python -m cpc_agent.main [--answers answers.json].

Wires together every pipeline stage in the order set out in CLAUDE.md §4:
scope gate -> validate -> calculate/flag -> screen comments -> investigate
(LLM) -> evidence status -> findings review gate -> brief -> final approval
gate. HALT stops the run immediately; PAUSE asks the analyst (interactively
or via the scripted answers file) and records the decision; LIMITATION is
recorded and the run continues.

`prepare_run_for_review` holds everything up to (but not including) the
findings review gate — validation, metrics/flagging, screening,
investigation, evidence records. `run_pipeline` (the CLI) calls it and then
continues straight through review, brief and final approval in one
synchronous run. The web dashboard (dashboard_state.py) calls the same
`prepare_run_for_review` to start a run, then drives review/brief/approval
across separate HTTP requests instead — both paths share every pipeline
stage up to that point, so there is exactly one implementation of
validation/metrics/screening/investigation to keep correct and tested.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import audit, config
from .brief import populate_cpc_template, render_brief_html, update_brief_status
from .evidence import EvidenceRecord, build_evidence_record
from .gates import Gates
from .investigate import run_investigation
from .loaders import load_approved_sources
from .metrics import Signal, calculate_metrics, flag_signals
from .screening import EligibleComment, quarantine_summary, screen_comments
from .validation import (
    HaltError,
    ScopeContext,
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


@dataclass
class ReviewReadyRun:
    """Everything the findings-review stage onward needs. `status` is
    "ready" (proceed to review), "aborted" (analyst declined at the scope
    gate) or "halted" (HaltError, or the analyst aborted on a PAUSE) — in
    both non-"ready" cases `message` explains why and the audit log has
    already recorded and closed the event."""

    run_id: str
    runs_dir: Path
    status: str
    message: str = ""
    gates: Gates | None = None
    context: ScopeContext | None = None
    signals: list[Signal] = field(default_factory=list)
    evidence_records: list[EvidenceRecord] = field(default_factory=list)
    exceptions: dict = field(default_factory=dict)
    comments_by_id: dict[str, EligibleComment] = field(default_factory=dict)
    data_version: str = ""
    snapshot_date: str = "n/a"

    @property
    def run_dir(self) -> Path:
        return self.runs_dir / self.run_id


def prepare_run_for_review(
    answers_path: str | None = None,
    answers: dict | None = None,
    data_dir: Path = config.DATA_DIR,
    runs_dir: Path = config.RUNS_DIR,
    client=None,
    model: str | None = None,
    run_id: str | None = None,
) -> ReviewReadyRun:
    run_id = run_id or audit.new_run_id()
    log = audit.AuditLog(run_id, runs_dir=runs_dir)
    log.log_event(
        "run_start",
        instruction_version=config.INSTRUCTION_VERSION,
        scripted=answers_path is not None or answers is not None,
    )

    sources = load_approved_sources(data_dir)
    log.log_event("input_files", hashes=audit.input_file_hashes(sources))
    data_version = audit.compute_data_version(sources)

    gates = Gates(answers_path, audit=log, answers=answers)

    context = check_reporting_context(sources)
    if not gates.confirm_scope(context):
        log.log_event("run_end", status="aborted_at_scope_gate")
        log.close()
        return ReviewReadyRun(run_id=run_id, runs_dir=runs_dir, status="aborted", message="Run aborted at the scope gate.")

    try:
        validate_schema(sources)
    except HaltError as exc:
        log.log_event("halt", reason=str(exc))
        log.log_event("run_end", status="halted")
        log.close()
        return ReviewReadyRun(run_id=run_id, runs_dir=runs_dir, status="halted", message=f"HALT: {exc}")

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
            return ReviewReadyRun(
                run_id=run_id, runs_dir=runs_dir, status="halted",
                message=f"HALT: analyst aborted on blank value for {issue.issue_key}",
            )
        excluded[issue.file].add(tuple(issue.segment.values()))
        _record_exclusion(issue.issue_key, issue.segment, issue.file, f"blank '{issue.field}'", blank_value_summary(issue))

    for issue in reconcile_status_counts(sources.get("course_metrics").rows):
        decision = gates.resolve_reconciliation(issue)
        if decision == "abort":
            log.log_event("halt", reason=f"Analyst aborted on reconciliation mismatch: {issue.issue_key}")
            log.log_event("run_end", status="halted")
            log.close()
            return ReviewReadyRun(
                run_id=run_id, runs_dir=runs_dir, status="halted",
                message=f"HALT: analyst aborted on reconciliation mismatch for {issue.issue_key}",
            )
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
        audit_log_event_fn=lambda event, payload: log.log_event(event, **payload),
        client=client,
        model=model,
    )
    if signals and screening_result.eligible and not investigation.finished:
        detail = "Investigation loop ended without an explicit finish_investigation call."
        summary = "The evidence-gathering step ended before every finding could be reviewed."
        exceptions["limitations"].append({"detail": detail, "summary": summary})
        log.log_event("limitation", detail=detail, summary=summary)

    for signal_id, texts in investigation.limitations.items():
        for text in texts:
            summary = f"{signal_id}: {text}"
            exceptions["limitations"].append({"signal_id": signal_id, "detail": summary, "summary": summary})
            log.log_event("investigation_limitation", signal_id=signal_id, summary=summary)

    evidence_records = []
    for signal in signals:
        extra_limitation = " ".join(investigation.limitations.get(signal.id, []))
        record = build_evidence_record(
            signal,
            aligned_ids=investigation.aligned_ids(signal.id),
            countervailing_ids=investigation.countervailing_ids(signal.id),
            themes=investigation.themes(signal.id),
            extra_limitation=extra_limitation,
        )
        evidence_records.append(record)
        log.log_event("evidence_status", finding_id=signal.id, status=record.evidence_status)

    comments_by_id = {c.comment_id: c for c in screening_result.eligible}

    snapshot_date = next(
        (row["snapshot_date"] for row in sources.get("course_metrics").rows if row.get("reporting_period") == config.CURRENT_PERIOD),
        "n/a",
    )

    # A snapshot of everything gathered so far, so a caller that only reads
    # files (the dashboard, across separate HTTP requests) has something to
    # show immediately. The CLI overwrites evidence_records.json/
    # exception_log.json again once review/approval finish.
    (log.run_dir / "evidence_records.json").write_text(
        json.dumps([r.to_dict() for r in evidence_records], indent=2), encoding="utf-8"
    )
    (log.run_dir / "exception_log.json").write_text(json.dumps(exceptions, indent=2), encoding="utf-8")
    (log.run_dir / "eligible_comments.json").write_text(
        json.dumps(
            {
                cid: {"mode": c.delivery_mode, "region": c.delivery_region, "prompt": c.prompt, "text": c.text}
                for cid, c in comments_by_id.items()
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    log.close()
    return ReviewReadyRun(
        run_id=run_id,
        runs_dir=runs_dir,
        status="ready",
        gates=gates,
        context=context,
        signals=signals,
        evidence_records=evidence_records,
        exceptions=exceptions,
        comments_by_id=comments_by_id,
        data_version=data_version,
        snapshot_date=snapshot_date,
    )


def run_pipeline(
    answers_path: str | None = None,
    data_dir: Path = config.DATA_DIR,
    runs_dir: Path = config.RUNS_DIR,
    client=None,
    model: str | None = None,
) -> int:
    prep = prepare_run_for_review(answers_path=answers_path, data_dir=data_dir, runs_dir=runs_dir, client=client, model=model)
    if prep.status != "ready":
        print(prep.message)
        return 1

    log = audit.AuditLog(prep.run_id, runs_dir=runs_dir)
    gates = prep.gates
    gates.audit = log  # prep's AuditLog was closed after prepare_run_for_review; point at the reopened one
    reviewed = gates.review_findings(prep.evidence_records, prep.comments_by_id)
    accepted = [r for r in reviewed if r.reviewer_status in ("accept", "edit")]

    generated_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    brief_path = populate_cpc_template(
        accepted,
        prep.context,
        prep.run_id,
        prep.data_version,
        config.INSTRUCTION_VERSION,
        generated_date,
        prep.snapshot_date,
        prep.exceptions,
        log.run_dir,
        analyst_name=gates.analyst_name,
    )
    log.log_event("brief_written", path=str(brief_path))

    decision = gates.final_approval(brief_path)
    update_brief_status(brief_path, decision)

    html_path = render_brief_html(
        accepted,
        prep.context,
        prep.run_id,
        prep.data_version,
        generated_date,
        prep.snapshot_date,
        prep.exceptions,
        gates.analyst_name,
        decision,
        prep.comments_by_id,
        log.run_dir,
    )
    log.log_event("brief_html_written", path=str(html_path))

    (log.run_dir / "evidence_records.json").write_text(
        json.dumps([r.to_dict() for r in reviewed], indent=2), encoding="utf-8"
    )
    (log.run_dir / "exception_log.json").write_text(json.dumps(prep.exceptions, indent=2), encoding="utf-8")

    log.log_event("run_end", status="completed", final_decision=decision)
    log.close()

    print(f"Run {prep.run_id} complete. Outputs written to {log.run_dir}")
    print(f"Final decision: {decision}. The brief is not sent, emailed or published.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return run_pipeline(answers_path=args.answers)


if __name__ == "__main__":
    sys.exit(main())
