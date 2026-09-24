"""Schema validation, scope context, blank-value and reconciliation checks.

Deterministic only — no LLM calls. Pipeline states per CLAUDE.md:
  HALT  -> validate_schema raises HaltError; the run must stop.
  PAUSE -> find_blank_required_values / reconcile_status_counts /
           check_satisfaction_sample_size return issues; gates.py asks the
           analyst what to do with each one.
  LIMITATION -> missing_optional_files returns keys to record and continue.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import config
from .loaders import LoadedSources


class HaltError(Exception):
    """Raised for conditions that must stop the run: a missing required file
    or a missing required column."""


@dataclass
class ScopeContext:
    course_code: str
    course_title: str
    current_period: str
    previous_period: str
    files_found: list[tuple[str, str, bool]]  # (key, filename, present)


@dataclass
class BlankValueIssue:
    file: str
    row_index: int
    field: str
    segment: dict[str, str]
    issue_key: str


@dataclass
class ReconciliationIssue:
    file: str
    row_index: int
    segment: dict[str, str]
    enrolments: float
    sum_of_status_fields: float
    difference: float
    issue_key: str


@dataclass
class LowSampleIssue:
    file: str
    row_index: int
    segment: dict[str, str]
    n_respondents: float
    issue_key: str


def _is_blank(value: str | None) -> bool:
    return value is None or value.strip() == ""


# --- Plain-language summaries -------------------------------------------
#
# These produce the wording shown to a human reader (the brief's "What's
# not included" section). The technical detail string built in main.py
# (field names, key=value pairs) stays in exception_log.json only — it is
# never shown in the brief.

_FIELD_READABLE = {
    "withdrawals": "withdrawal",
    "enrolments": "enrolment",
    "completions": "completion",
    "in_progress": "in-progress",
    "commencements": "commencement",
    "n_respondents": "respondent count",
    "pct_satisfied": "satisfaction",
    "organisation_avg_pct": "organisation average",
}


def _field_readable(field_name: str) -> str:
    return _FIELD_READABLE.get(field_name, field_name.replace("_", " "))


def _segment_phrase(segment: dict[str, str]) -> str:
    """A short plain-language phrase identifying a segment, e.g. "Region C
    blended delivery", "Online teacher support results", "CWSU204"."""
    if "delivery_region" in segment and "delivery_mode" in segment:
        return f"{segment['delivery_region']} {segment['delivery_mode'].lower()} delivery"
    if "delivery_mode" in segment and "survey_item" in segment:
        item = segment["survey_item"].replace("_", " ")
        return f"{segment['delivery_mode']} {item} results"
    if "unit_code" in segment:
        return segment["unit_code"]
    return ", ".join(f"{k}={v}" for k, v in segment.items())


def blank_value_summary(issue: "BlankValueIssue") -> str:
    period = issue.segment.get("reporting_period", "")
    return f"{_segment_phrase(issue.segment)} was excluded because the {period} {_field_readable(issue.field)} figure was missing."


def reconciliation_summary(issue: "ReconciliationIssue") -> str:
    period = issue.segment.get("reporting_period", "")
    return (
        f"{_segment_phrase(issue.segment)} was excluded because its {period} enrolment figures "
        "didn't add up (a reconciliation check failed)."
    )


def low_sample_excluded_summary(issue: "LowSampleIssue") -> str:
    period = issue.segment.get("reporting_period", "")
    return (
        f"{_segment_phrase(issue.segment)} was excluded because fewer than "
        f"{config.SATISFACTION_MIN_N} people responded in {period}."
    )


def low_sample_proceeded_summary(issue: "LowSampleIssue") -> str:
    period = issue.segment.get("reporting_period", "")
    return (
        f"{_segment_phrase(issue.segment)} was included with caution because fewer than "
        f"{config.SATISFACTION_MIN_N} people responded in {period}."
    )


def validate_schema(sources: LoadedSources) -> None:
    """Raises HaltError if a required file, or a required column within any
    present file (required or optional), is missing."""
    missing_files = [
        filename
        for key, filename in config.REQUIRED_FILES.items()
        if not sources.get(key).present
    ]
    if missing_files:
        raise HaltError(
            "Required source file(s) missing: " + ", ".join(sorted(missing_files))
        )

    column_problems: list[str] = []
    for key, filename in config.ALL_FILES.items():
        loaded = sources.get(key)
        if not loaded.present:
            continue
        required_columns = config.REQUIRED_COLUMNS.get(filename, [])
        missing_columns = [c for c in required_columns if c not in loaded.columns]
        if missing_columns:
            column_problems.append(f"{filename}: missing column(s) {', '.join(missing_columns)}")
    if column_problems:
        raise HaltError("Required column(s) missing: " + "; ".join(column_problems))


def missing_optional_files(sources: LoadedSources) -> list[str]:
    """Returns the keys (e.g. 'student_feedback') of optional files that are
    absent, for recording as a run LIMITATION."""
    return [key for key in config.OPTIONAL_FILES if not sources.get(key).present]


def check_reporting_context(sources: LoadedSources) -> ScopeContext:
    files_found = [
        (key, filename, sources.get(key).present)
        for key, filename in config.ALL_FILES.items()
    ]
    return ScopeContext(
        course_code=config.COURSE_CODE,
        course_title=config.COURSE_TITLE,
        current_period=config.CURRENT_PERIOD,
        previous_period=config.PREVIOUS_PERIOD,
        files_found=files_found,
    )


def _segment_of(filename: str, row: dict[str, str]) -> dict[str, str]:
    if filename == "course_metrics.csv":
        return {k: row.get(k, "") for k in ("reporting_period", "delivery_region", "delivery_mode")}
    if filename == "satisfaction.csv":
        return {k: row.get(k, "") for k in ("reporting_period", "delivery_mode", "survey_item")}
    if filename == "unit_results.csv":
        return {k: row.get(k, "") for k in ("reporting_period", "unit_code")}
    return {}


def segment_of(filename: str, row: dict[str, str]) -> dict[str, str]:
    """Public wrapper for the segment-identifying fields of a row, so
    callers (e.g. main.py, applying an analyst's exclude decision) can
    build the same key used internally without duplicating field lists."""
    return _segment_of(filename, row)


def find_blank_required_values(sources: LoadedSources) -> list[BlankValueIssue]:
    """Finds blank values in required-value fields of the three required
    files. Never estimates or fills a value — the caller (gates.py) must ask
    the analyst to exclude the segment or abort the run."""
    issues: list[BlankValueIssue] = []
    for key, filename in config.REQUIRED_FILES.items():
        loaded = sources.get(key)
        if not loaded.present:
            continue
        required_value_fields = config.REQUIRED_VALUE_FIELDS.get(filename, [])
        for row_index, row in enumerate(loaded.rows):
            segment = _segment_of(filename, row)
            for field_name in required_value_fields:
                if _is_blank(row.get(field_name)):
                    issue_key = f"{filename}:" + ":".join(segment.values()) + f":{field_name}"
                    issues.append(
                        BlankValueIssue(
                            file=filename,
                            row_index=row_index,
                            field=field_name,
                            segment=segment,
                            issue_key=issue_key,
                        )
                    )
    return issues


def reconcile_status_counts(
    rows: list[dict[str, str]], excluded_row_indexes: set[int] | None = None
) -> list[ReconciliationIssue]:
    """For course_metrics.csv rows: completions + in_progress + withdrawals
    must equal enrolments (commencements excluded from the check). Rows
    already excluded for a blank required value are skipped."""
    excluded_row_indexes = excluded_row_indexes or set()
    issues: list[ReconciliationIssue] = []
    for row_index, row in enumerate(rows):
        if row_index in excluded_row_indexes:
            continue
        raw_values = [row.get(f) for f in [config.RECONCILIATION_TOTAL_FIELD, *config.RECONCILIATION_FIELDS]]
        if any(_is_blank(v) for v in raw_values):
            # A blank here is already reported by find_blank_required_values.
            continue
        enrolments = float(row[config.RECONCILIATION_TOTAL_FIELD])
        total = sum(float(row[f]) for f in config.RECONCILIATION_FIELDS)
        if total != enrolments:
            segment = _segment_of("course_metrics.csv", row)
            issue_key = "course_metrics.csv:" + ":".join(segment.values()) + ":reconciliation"
            issues.append(
                ReconciliationIssue(
                    file="course_metrics.csv",
                    row_index=row_index,
                    segment=segment,
                    enrolments=enrolments,
                    sum_of_status_fields=total,
                    difference=total - enrolments,
                    issue_key=issue_key,
                )
            )
    return issues


def check_satisfaction_sample_size(rows: list[dict[str, str]]) -> list[LowSampleIssue]:
    """Rows with n_respondents below config.SATISFACTION_MIN_N must PAUSE
    before being interpreted."""
    issues: list[LowSampleIssue] = []
    for row_index, row in enumerate(rows):
        raw_n = row.get("n_respondents")
        if _is_blank(raw_n):
            continue  # reported by find_blank_required_values
        n_respondents = float(raw_n)
        if n_respondents < config.SATISFACTION_MIN_N:
            segment = _segment_of("satisfaction.csv", row)
            issue_key = "satisfaction.csv:" + ":".join(segment.values()) + ":n_respondents"
            issues.append(
                LowSampleIssue(
                    file="satisfaction.csv",
                    row_index=row_index,
                    segment=segment,
                    n_respondents=n_respondents,
                    issue_key=issue_key,
                )
            )
    return issues
