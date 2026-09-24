"""Fills data/cpc_brief_template.txt from accepted/edited evidence records only."""
from __future__ import annotations

from pathlib import Path

from . import config
from .evidence import EvidenceRecord
from .validation import ScopeContext

STATUS_BY_DECISION = {
    "pending": "Pending final approval",
    "approve": "Approved",
    "return": "Returned",
}


def _scope_text(context: ScopeContext) -> str:
    present = [filename for _key, filename, is_present in context.files_found if is_present]
    missing = [filename for _key, filename, is_present in context.files_found if not is_present]
    lines = [
        f"Course {context.course_code} — {context.course_title}. "
        f"Period {context.current_period}, compared with {context.previous_period}.",
        "Files used: " + ", ".join(present) + ".",
    ]
    if missing:
        lines.append("Not available: " + ", ".join(missing) + ".")
    return "\n".join(lines)


def _signal_table_row(record: EvidenceRecord) -> str:
    return (
        f"| {record.finding_id} | {record.measure} ({record.segment}) | {record.calculation} "
        f"| {record.comparison_rule} | {record.source_file}, {record.period}, n={record.n:.0f} "
        f"| {record.evidence_status} |"
    )


def _feedback_block(record: EvidenceRecord) -> str:
    lines = [f"**{record.finding_id}** — {record.measure} ({record.segment})"]
    lines.append(f"- Aligned: {', '.join(record.aligned_ids) if record.aligned_ids else 'none'}")
    lines.append(
        f"- Countervailing: {', '.join(record.countervailing_ids) if record.countervailing_ids else 'none'}"
    )
    lines.append(f"- Theme(s): {', '.join(record.themes) if record.themes else 'none'}")
    lines.append(f"- Limitation: {record.limitation}")
    if record.reviewer_note:
        lines.append(f"- Reviewer note: {record.reviewer_note}")
    return "\n".join(lines)


def _exception_lines(exceptions: dict) -> list[str]:
    lines: list[str] = []
    for entry in exceptions.get("exclusions", []):
        lines.append(f"- {entry['detail']}")
    for entry in exceptions.get("quarantined", []):
        lines.append(f"- {entry['detail']}")
    for entry in exceptions.get("limitations", []):
        lines.append(f"- {entry['detail']}")
    return lines


def _question_for(record: EvidenceRecord) -> str:
    if record.measure == "unit_withdrawal_rate":
        text = f"What conditions around {record.segment} might be relevant to its withdrawal rate in {record.period}?"
    elif record.measure == "segment_withdrawal_rate":
        text = (
            f"What support or delivery conditions might be relevant to the change in withdrawal "
            f"rate for {record.segment} learners in {record.period}?"
        )
    else:
        measure_label = record.measure.replace("_", " ")
        text = (
            f"What support or delivery conditions might be relevant to {measure_label} satisfaction "
            f"for {record.segment} learners in {record.period}?"
        )
    return f"- {record.finding_id}: {text}"


def populate_cpc_template(
    accepted_records: list[EvidenceRecord],
    context: ScopeContext,
    run_id: str,
    data_version: str,
    instruction_version: str,
    generated_date: str,
    snapshot_date: str,
    exceptions: dict,
    output_dir: Path,
    template_path: Path = config.TEMPLATE_PATH,
) -> Path:
    """Writes runs/<run_id>/brief.md from accepted_records only. The brief
    is always written with status "Pending final approval" and decision
    "pending" — update_brief_status records the outcome of gate 9 once the
    analyst has actually seen the file."""
    template = template_path.read_text(encoding="utf-8")

    text = template
    text = text.replace("Course: [code and title]", f"Course: {context.course_code} ({context.course_title})")
    text = text.replace("Period: [ ]", f"Period: {context.current_period} (vs {context.previous_period})")
    text = text.replace("Data snapshot: [ ]", f"Data snapshot: {snapshot_date}")
    text = text.replace(
        "Status: Pending final approval / Approved / Returned", f"Status: {STATUS_BY_DECISION['pending']}"
    )
    text = text.replace("Run ID: [ ]", f"Run ID: {run_id}")
    text = text.replace("Input data version: [ ]", f"Input data version: {data_version}")
    text = text.replace("Instruction version: [ ]", f"Instruction version: {instruction_version}")
    text = text.replace("Generated: [date]", f"Generated: {generated_date}")
    text = text.replace("Analyst: [ ]", "Analyst: (recorded in audit_log.jsonl)")
    text = text.replace("Decision: pending / approved / returned", "Decision: pending")

    scope_anchor = "[Files used, periods, any sources unavailable]"
    text = text.replace(scope_anchor, scope_anchor + "\n" + _scope_text(context))

    table_anchor = "|---|---|---|---|---|---|"
    table_rows = "\n".join(_signal_table_row(r) for r in accepted_records) or "| — | no approved findings | | | | |"
    text = text.replace(table_anchor, table_anchor + "\n" + table_rows)

    feedback_anchor = "It does not establish cause."
    feedback_body = "\n\n".join(_feedback_block(r) for r in accepted_records) or "No approved findings."
    text = text.replace(feedback_anchor, feedback_anchor + "\n\n" + feedback_body)

    exceptions_anchor = "[Missing data, quarantined comments, low samples, unresolved issues]"
    exception_lines = _exception_lines(exceptions)
    exceptions_body = "\n".join(exception_lines) if exception_lines else "- None recorded."
    text = text.replace(exceptions_anchor, exceptions_anchor + "\n" + exceptions_body)

    questions_anchor = "[Neutral questions linked to signal IDs. No recommendations.]"
    questions_body = "\n".join(_question_for(r) for r in accepted_records) or "No approved findings to raise questions about."
    text = text.replace(questions_anchor, questions_anchor + "\n" + questions_body)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "brief.md"
    output_path.write_text(text, encoding="utf-8")
    return output_path


def update_brief_status(brief_path: Path, decision: str, analyst_label: str = "(recorded in audit_log.jsonl)") -> None:
    """Rewrites the Status/Decision lines in an already-written brief.md to
    reflect the final approval gate's outcome. Never sends, emails or
    publishes the file — it is only edited in place, locally."""
    text = brief_path.read_text(encoding="utf-8")
    status = STATUS_BY_DECISION.get(decision, STATUS_BY_DECISION["pending"])
    text = text.replace(f"Status: {STATUS_BY_DECISION['pending']}", f"Status: {status}")
    text = text.replace("Decision: pending", f"Decision: {decision}")
    text = text.replace("Analyst: (recorded in audit_log.jsonl)", f"Analyst: {analyst_label}")
    brief_path.write_text(text, encoding="utf-8")
