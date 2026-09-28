"""Fills data/cpc_brief_template.txt (brief.md) and the packaged
src/cpc_agent/templates/brief.css design (brief.html) from accepted/edited
evidence records only."""
from __future__ import annotations

import re
from datetime import datetime
from html import escape as _html_escape
from pathlib import Path

from . import config
from .evidence import EvidenceRecord, humanize_measure, readable_measure_label
from .screening import EligibleComment
from .validation import ScopeContext

STATUS_BY_DECISION = {
    "pending": "Pending final approval",
    "approve": "Approved",
    "return": "Returned",
}

DRAFT_TITLE = "# DRAFT — Course Performance Workshop Brief (not approved)"
APPROVED_TITLE = "# Course Performance Workshop Brief"

MAX_HTML_EXCERPTS = 2

# Plain-language descriptions of each source file, for the brief only —
# evidence_records.json/exception_log.json keep the literal filename
# (config.ALL_FILES); a workshop audience has no use for CSV filenames.
_SOURCE_DESCRIPTIONS: dict[str, str] = {
    "course_metrics.csv": "course enrolment and withdrawal records",
    "satisfaction.csv": "learner satisfaction survey results",
    "unit_results.csv": "unit enrolment and completion records",
    "student_feedback.csv": "learner feedback comments",
}


def _describe_source(filename: str) -> str:
    return _SOURCE_DESCRIPTIONS.get(filename, filename)


# A signal's comparison_rule sometimes carries a trailing technical
# eligibility check appended for evidence_records.json's own audit trail
# (e.g. "; n=380 ≥ 30", see metrics.flag_signals) — required there, but not
# something a workshop audience needs to parse. The brief drops it; the
# stored comparison_rule field is never modified.
_TECHNICAL_N_SUFFIX = re.compile(r";\s*n\s*=\s*\d+(?:\.\d+)?\s*[≥>=]+\s*\d+(?:\.\d+)?\s*$")


def _plain_rule_text(comparison_rule: str) -> str:
    return _TECHNICAL_N_SUFFIX.sub("", comparison_rule).strip()


def _rejected_lines(rejected_records: list[EvidenceRecord]) -> list[str]:
    """One plain-language line per rejected finding — measure and the
    analyst's own reason (reviewer_note, required for a "reject" decision
    — see gates.review_findings / dashboard_state.apply_finding_review)."""
    lines = []
    for record in rejected_records:
        label = readable_measure_label(record.measure, record.segment)
        reason = record.reviewer_note.strip() if record.reviewer_note else "no reason recorded"
        lines.append(f"{record.finding_id} ({label}) was reviewed and rejected by the analyst: {reason}")
    return lines


def _scope_text(context: ScopeContext) -> str:
    present = [_describe_source(filename) for _key, filename, is_present in context.files_found if is_present]
    missing = [_describe_source(filename) for _key, filename, is_present in context.files_found if not is_present]
    lines = [
        f"Course {context.course_code} — {context.course_title}. "
        f"Period {context.current_period}, compared with {context.previous_period}.",
        "Sources used: " + ", ".join(present) + ".",
    ]
    if missing:
        lines.append("Not available: " + ", ".join(missing) + ".")
    return "\n".join(lines)


def _signal_table_row(record: EvidenceRecord) -> str:
    label = readable_measure_label(record.measure, record.segment)
    return (
        f"| {record.finding_id} | {label} | {record.calculation} "
        f"| {_plain_rule_text(record.comparison_rule)} "
        f"| {_describe_source(record.source_file)}, {record.period}, n={record.n:.0f} "
        f"| {record.evidence_status} |"
    )


def _feedback_block(record: EvidenceRecord) -> str:
    label = readable_measure_label(record.measure, record.segment)
    lines = [f"**{record.finding_id}** — {label}"]
    lines.append(f"- Aligned: {', '.join(record.aligned_ids) if record.aligned_ids else 'none'}")
    lines.append(
        f"- Countervailing: {', '.join(record.countervailing_ids) if record.countervailing_ids else 'none'}"
    )
    lines.append(f"- Theme(s): {', '.join(record.themes) if record.themes else 'none'}")
    lines.append(f"- Limitation: {record.limitation}")
    if record.reviewer_note:
        lines.append(f"- Reviewer note: {record.reviewer_note}")
    return "\n".join(lines)


def _exception_summaries(exceptions: dict) -> list[str]:
    """Plain-language summaries only — never the technical `detail` string
    (field names, key=value pairs), which stays in exception_log.json."""
    summaries: list[str] = []
    for entry in exceptions.get("exclusions", []):
        summaries.append(entry.get("summary", entry["detail"]))
    for entry in exceptions.get("quarantined", []):
        summaries.append(entry.get("summary", entry["detail"]))
    for entry in exceptions.get("limitations", []):
        summaries.append(entry.get("summary", entry["detail"]))
    return summaries


def _exception_lines(exceptions: dict, rejected_records: list[EvidenceRecord] | None = None) -> list[str]:
    summaries = _exception_summaries(exceptions) + _rejected_lines(rejected_records or [])
    return [f"- {summary}" for summary in summaries]


def _question_text(record: EvidenceRecord) -> str:
    if record.measure == "unit_withdrawal_rate":
        return f"What conditions around {record.segment} might be relevant to its withdrawal rate in {record.period}?"
    if record.measure == "segment_withdrawal_rate":
        return (
            f"What support or delivery conditions might be relevant to the change in withdrawal "
            f"rate for {record.segment} learners in {record.period}?"
        )
    measure_label = humanize_measure(record.measure)
    return (
        f"What support or delivery conditions might be relevant to {measure_label} satisfaction "
        f"for {record.segment} learners in {record.period}?"
    )


def _question_for(record: EvidenceRecord) -> str:
    return f"- {record.finding_id}: {_question_text(record)}"


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
    analyst_name: str = "(not provided)",
    template_path: Path = config.TEMPLATE_PATH,
    rejected_records: list[EvidenceRecord] | None = None,
) -> Path:
    """Writes runs/<run_id>/brief.md from accepted_records only. The brief
    is always written with status "Pending final approval" and decision
    "pending" — update_brief_status records the outcome of gate 9 once the
    analyst has actually seen the file.

    rejected_records is optional and only ever used to list what's not
    included (section 4) — a rejected finding never appears in the signal
    table, the feedback section or the workshop questions, only as a plain
    line naming its measure and the analyst's reason.

    Every template placeholder line written in square brackets is replaced
    by its generated content (the placeholder text itself is removed, not
    kept as a leftover caption)."""
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
    text = text.replace("Analyst: [ ]", f"Analyst: {analyst_name}")
    text = text.replace("Decision: pending / approved / returned", "Decision: pending")

    text = text.replace("[Files used, periods, any sources unavailable]", _scope_text(context))

    section2_caption = "[Only findings the analyst has approved. Detailed evidence records are kept separately.]\n"
    text = text.replace(section2_caption, "")

    table_anchor = "|---|---|---|---|---|---|"
    table_rows = "\n".join(_signal_table_row(r) for r in accepted_records) or "| — | no approved findings | | | | |"
    text = text.replace(table_anchor, table_anchor + "\n" + table_rows)

    feedback_anchor = "It does not establish cause."
    feedback_body = "\n\n".join(_feedback_block(r) for r in accepted_records) or "No approved findings."
    text = text.replace(feedback_anchor, feedback_anchor + "\n\n" + feedback_body)

    exception_lines = _exception_lines(exceptions, rejected_records)
    exceptions_body = "\n".join(exception_lines) if exception_lines else "- None recorded."
    text = text.replace("[Missing data, quarantined comments, low samples, unresolved issues]", exceptions_body)

    questions_body = "\n".join(_question_for(r) for r in accepted_records) or "No approved findings to raise questions about."
    text = text.replace("[Neutral questions linked to signal IDs. No recommendations.]", questions_body)

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "brief.md"
    output_path.write_text(text, encoding="utf-8")
    return output_path


def update_brief_status(brief_path: Path, decision: str) -> None:
    """Rewrites the Status/Decision lines in an already-written brief.md to
    reflect the final approval gate's outcome. Never sends, emails or
    publishes the file — it is only edited in place, locally.

    On "approve", the "DRAFT" marker and "(not approved)" qualifier are
    removed from the header, since the brief is no longer a draft awaiting
    approval. On "return" (or if the brief is never approved), both are
    left in place and the Status line reads "Returned"."""
    text = brief_path.read_text(encoding="utf-8")
    status = STATUS_BY_DECISION.get(decision, STATUS_BY_DECISION["pending"])
    text = text.replace(f"Status: {STATUS_BY_DECISION['pending']}", f"Status: {status}")
    text = text.replace("Decision: pending", f"Decision: {decision}")
    if decision == "approve":
        text = text.replace(DRAFT_TITLE, APPROVED_TITLE)
    brief_path.write_text(text, encoding="utf-8")


# --- brief.html: docs/brief_mockup.html's design, filled from run data ----
#
# The mockup is the single source of truth for styling (fonts, colours,
# spacing, one-A4-page layout) — its <style> block is read verbatim and
# reused here. The markup structure below mirrors the mockup's, but is
# built per accepted record rather than hard-coded for a fixed signal
# count, since a real run can flag any number of signals.


def _escape(value: object) -> str:
    # quote=False: everything below is inserted into text content, never
    # into an HTML attribute, so quotes need no escaping — and leaving
    # them as literal ' / " keeps comment excerpts exactly as written.
    return _html_escape(str(value), quote=False)


def _format_date(iso_date: str) -> str:
    try:
        parsed = datetime.strptime(iso_date, "%Y-%m-%d")
    except ValueError:
        return iso_date
    return f"{parsed.day} {parsed.strftime('%B %Y')}"


def _load_brief_css(css_path: Path) -> str:
    """Reads the packaged brief stylesheet (src/cpc_agent/templates/brief.css)
    and wraps it in a <style> tag. Reading from the package (not
    docs/brief_mockup.html) means brief.html generation never depends on a
    docs/ file being present at runtime; the mockup stays in docs/ purely
    as a human-readable design preview."""
    css_text = css_path.read_text(encoding="utf-8")
    return f"<style>\n{css_text}</style>"


def _signal_kind(measure: str) -> str:
    if measure == "unit_withdrawal_rate":
        return "unit"
    if measure == "segment_withdrawal_rate":
        return "segment"
    return "satisfaction"


def _measure_id_line(record: EvidenceRecord) -> str:
    kind = _signal_kind(record.measure)
    n_fmt = f"{record.n:,.0f}"
    if kind == "satisfaction":
        return f"{record.finding_id} · learner survey, {record.segment} (n={n_fmt})"
    if kind == "unit":
        return f"{record.finding_id} · unit results (n={n_fmt} enrolments)"
    return f"{record.finding_id} · enrolment records, {record.segment} (n={n_fmt})"


def _measure_title(record: EvidenceRecord) -> str:
    kind = _signal_kind(record.measure)
    if kind == "satisfaction":
        return f"{humanize_measure(record.measure).capitalize()} satisfaction"
    if kind == "unit":
        unit_title = record.details.get("unit_title", "")
        return f"Withdrawals from {record.segment} {unit_title}".strip()
    return f"{record.segment} withdrawal rate"


def _value_suffix(record: EvidenceRecord) -> str:
    return "satisfied" if _signal_kind(record.measure) == "satisfaction" else "withdrew"


def _gauge_marks(record: EvidenceRecord) -> list[tuple[float, str]]:
    kind = _signal_kind(record.measure)
    details = record.details
    marks: list[tuple[float, str]] = []
    if kind == "satisfaction":
        prev = details.get("prev_pct_satisfied")
        if prev is not None:
            marks.append((prev, f"{config.PREVIOUS_PERIOD}: {prev:.0f}%"))
        org_avg = details.get("organisation_avg_pct")
        if org_avg is not None:
            marks.append((org_avg, f"avg {org_avg:.0f}%"))
    elif kind == "unit":
        median = details.get("median_unit_withdrawal_rate")
        if median is not None:
            marks.append((median, f"typical unit: {median:.1f}%"))
    else:
        prev_rate = details.get("prev_rate")
        if prev_rate is not None:
            marks.append((prev_rate, f"{config.PREVIOUS_PERIOD}: {prev_rate:.0f}%"))
    return marks


def _render_gauge(value: float, marks: list[tuple[float, str]]) -> str:
    ordered = sorted(marks, key=lambda m: m[0])
    mark_divs = []
    for index, (position, label) in enumerate(ordered):
        # Nudge a label's second close-by mark upward so the two don't overlap
        # (mirrors the mockup's handling of e.g. a "2024: 66%" mark next to "avg 72%").
        bumped = index > 0 and (position - ordered[index - 1][0]) < 8
        span_style = ' style="top:-3.4mm"' if bumped else ""
        mark_divs.append(f'<div class="mark" style="left:{position:.4g}%"><span{span_style}>{_escape(label)}</span></div>')
    marks_html = "\n        ".join(mark_divs)
    fill_width = max(0.0, min(100.0, value))
    return (
        '<div class="gauge">\n'
        '        <div class="track"></div>\n'
        f'        <div class="fill" style="width:{fill_width:.4g}%"></div>\n'
        f"        {marks_html}\n"
        "      </div>"
    )


def _render_measure_column(record: EvidenceRecord) -> str:
    gauge_html = _render_gauge(record.value, _gauge_marks(record))
    return (
        '<div class="measure">\n'
        f'      <div class="id">{_escape(_measure_id_line(record))}</div>\n'
        f"      <h3>{_escape(_measure_title(record))}</h3>\n"
        f'      <div class="value">{record.value:.0f}%<small>{_value_suffix(record)}</small></div>\n'
        f"      {gauge_html}\n"
        f'      <div class="rule">{_escape(_plain_rule_text(record.comparison_rule))}</div>\n'
        "    </div>"
    )


def _excerpt_blockquotes(
    excerpt_ids: list[str],
    comments_by_id: dict[str, EligibleComment],
    other: bool,
    ambiguous_links: dict[str, str],
) -> str:
    css_class = ' class="other"' if other else ""
    parts = []
    for comment_id in excerpt_ids[:MAX_HTML_EXCERPTS]:
        comment = comments_by_id.get(comment_id)
        # comments_by_id only ever holds eligible (non-quarantined) comments
        # (built from screening_result.eligible in main.py) — a quarantined
        # or otherwise-unknown id can only ever fall back to the placeholder.
        text = _escape(comment.text) if comment is not None else "(comment text unavailable)"
        ambiguous_note = ambiguous_links.get(comment_id)
        note_html = (
            f'<span class="ambiguous-note">Ambiguous: {_escape(ambiguous_note)}</span>' if ambiguous_note else ""
        )
        parts.append(f"<blockquote{css_class}>{text}<cite>{_escape(comment_id)}</cite>{note_html}</blockquote>")
    return "\n        ".join(parts)


def _render_evidence_column(record: EvidenceRecord, comments_by_id: dict[str, EligibleComment]) -> str:
    aligned_count = len(record.aligned_ids)
    countervailing_count = len(record.countervailing_ids)
    total = aligned_count + countervailing_count

    blocks: list[str] = []
    if aligned_count:
        full_width = ' style="flex-basis:100%"' if not countervailing_count else ""
        blocks.append(
            f"<div{full_width}>\n"
            f'        <h4>Comments consistent with this result ({aligned_count})</h4>\n'
            f"        {_excerpt_blockquotes(record.aligned_excerpt_ids, comments_by_id, False, record.ambiguous_links)}\n"
            + "      </div>"
        )
    if countervailing_count:
        full_width = ' style="flex-basis:100%"' if not aligned_count else ""
        blocks.append(
            f"<div{full_width}>\n"
            f'        <h4 class="other">Comments pointing the other way ({countervailing_count})</h4>\n'
            f"        {_excerpt_blockquotes(record.countervailing_excerpt_ids, comments_by_id, True, record.ambiguous_links)}\n"
            + "      </div>"
        )
    if not blocks:
        blocks.append(
            '<div style="flex-basis:100%">\n'
            "        <h4>Comments</h4>\n"
            '        <p class="none">No student comments relate to this finding. The group may need '
            "other sources, such as teacher or unit coordinator input.</p>\n"
            "      </div>"
        )

    if total == 0:
        status_html = '<div class="status">No comment evidence</div>'
    else:
        plural = "s" if total != 1 else ""
        status_html = f'<div class="status">{_escape(record.evidence_status)} evidence <em>· {total} comment{plural}</em></div>'
    question_html = f'<div class="question">{_escape(_question_text(record))}</div>'

    blocks_html = "\n      ".join(blocks)
    return (
        '<div class="evidence">\n'
        f"      {blocks_html}\n"
        '      <div class="footer-row">\n'
        f"        {status_html}\n"
        f"        {question_html}\n"
        "      </div>\n"
        "    </div>"
    )


def _render_signal_article(record: EvidenceRecord, comments_by_id: dict[str, EligibleComment]) -> str:
    return (
        '<article class="signal">\n'
        f"    {_render_measure_column(record)}\n"
        f"    {_render_evidence_column(record, comments_by_id)}\n"
        "  </article>"
    )


def _render_header(context: ScopeContext, analyst_name: str, decision: str, generated_date: str, snapshot_date: str) -> str:
    generated_readable = _format_date(generated_date)
    if decision == "approve":
        approval_lines = f"Approved for the workshop by\n      <strong>{_escape(analyst_name)}</strong>\n      {_escape(generated_readable)}"
    elif decision == "return":
        approval_lines = f"Returned for revision by\n      <strong>{_escape(analyst_name)}</strong>\n      {_escape(generated_readable)}"
    else:
        approval_lines = f"Prepared by\n      <strong>{_escape(analyst_name)}</strong>\n      Pending approval"
    return (
        "<header>\n"
        "    <div>\n"
        "      <h1>Course performance workshop brief</h1>\n"
        f'      <p class="course">{_escape(context.course_code)} {_escape(context.course_title)}</p>\n'
        f'      <p class="meta">{_escape(context.current_period)} results compared with '
        f"{_escape(context.previous_period)}, data to {_escape(_format_date(snapshot_date))}</p>\n"
        "    </div>\n"
        '    <div class="approval">\n'
        f"      {approval_lines}\n"
        "    </div>\n"
        "  </header>"
    )


def _render_context_section(
    context: ScopeContext, exceptions: dict, rejected_records: list[EvidenceRecord] | None = None
) -> str:
    present = [_describe_source(filename) for _key, filename, is_present in context.files_found if is_present]
    covers = (
        "Findings that crossed the review thresholds this run, with the student comments that relate "
        f"to each. Figures come from {', '.join(present)}."
    )
    items = _exception_summaries(exceptions) + _rejected_lines(rejected_records or [])
    if items:
        items_html = "\n        ".join(f"<li>{_escape(item)}</li>" for item in items)
    else:
        items_html = "<li>None recorded.</li>"
    return (
        '<section class="context">\n'
        "    <div>\n"
        "      <h2>What this brief covers</h2>\n"
        f"      <p>{_escape(covers)}</p>\n"
        "    </div>\n"
        "    <div>\n"
        "      <h2>What's not included</h2>\n"
        "      <ul>\n"
        f"        {items_html}\n"
        "      </ul>\n"
        "    </div>\n"
        "  </section>"
    )


def render_brief_html(
    accepted_records: list[EvidenceRecord],
    context: ScopeContext,
    run_id: str,
    data_version: str,
    generated_date: str,
    snapshot_date: str,
    exceptions: dict,
    analyst_name: str,
    decision: str,
    comments_by_id: dict[str, EligibleComment],
    output_dir: Path,
    css_path: Path = config.BRIEF_CSS_PATH,
    rejected_records: list[EvidenceRecord] | None = None,
) -> Path:
    """Writes runs/<run_id>/brief.html from accepted_records only, styled
    per src/cpc_agent/templates/brief.css (design source: docs/brief_mockup.html,
    kept there only as a human-readable preview — generation never reads
    that docs/ file), with every value, gauge position, excerpt, status and
    question filled from the run's own data. At most MAX_HTML_EXCERPTS
    comments per side are quoted, chosen at the findings review gate
    (EvidenceRecord.aligned_excerpt_ids/countervailing_excerpt_ids);
    quarantined comments can never appear because comments_by_id only ever
    contains eligible comments. No external resources (fonts, scripts) are
    loaded, so the brief renders correctly offline.

    rejected_records is optional and only ever used in "What's not
    included" — see populate_cpc_template."""
    style_block = _load_brief_css(css_path)
    header_html = _render_header(context, analyst_name, decision, generated_date, snapshot_date)
    context_html = _render_context_section(context, exceptions, rejected_records)
    reading_html = (
        '<p class="reading">Comments show what some students experienced. They can help the group '
        "decide what to look into, but they don't show why a result happened.</p>"
    )
    if accepted_records:
        signals_html = "\n\n  ".join(_render_signal_article(r, comments_by_id) for r in accepted_records)
    else:
        signals_html = '<p class="none">No approved findings.</p>'
    footer_html = (
        "<footer>\n"
        "    <span>Prepared by the CPC Preparation Agent (synthetic test data). Full evidence records "
        "are held with the run.</span>\n"
        f"    <span>Run {_escape(run_id)}, data {_escape(data_version)}</span>\n"
        "  </footer>"
    )

    html_doc = (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>Course performance workshop brief — {_escape(context.course_code)}</title>\n"
        f"{style_block}\n"
        "</head>\n"
        "<body>\n"
        '<main class="page">\n\n'
        f"  {header_html}\n\n"
        f"  {context_html}\n\n"
        f"  {reading_html}\n\n"
        f"  {signals_html}\n\n"
        f"  {footer_html}\n"
        "</main>\n"
        "</body>\n"
        "</html>\n"
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "brief.html"
    output_path.write_text(html_doc, encoding="utf-8")
    return output_path
