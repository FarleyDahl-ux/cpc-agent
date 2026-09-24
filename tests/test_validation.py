import csv
import shutil

import pytest

from cpc_agent import config
from cpc_agent.loaders import load_approved_sources
from cpc_agent.validation import (
    BlankValueIssue,
    HaltError,
    LowSampleIssue,
    ReconciliationIssue,
    blank_value_summary,
    check_satisfaction_sample_size,
    find_blank_required_values,
    low_sample_excluded_summary,
    low_sample_proceeded_summary,
    missing_optional_files,
    reconcile_status_counts,
    reconciliation_summary,
    validate_schema,
)


def test_validate_schema_passes_with_real_data():
    sources = load_approved_sources(config.DATA_DIR)
    validate_schema(sources)  # must not raise


def test_validate_schema_halts_on_missing_required_file(tmp_path):
    for name in ("satisfaction.csv", "unit_results.csv"):
        shutil.copy(config.DATA_DIR / name, tmp_path / name)
    sources = load_approved_sources(tmp_path)
    with pytest.raises(HaltError):
        validate_schema(sources)


def test_validate_schema_halts_on_missing_required_column(tmp_path):
    for name in config.REQUIRED_FILES.values():
        shutil.copy(config.DATA_DIR / name, tmp_path / name)
    # Rewrite course_metrics.csv without the withdrawals column.
    with (config.DATA_DIR / "course_metrics.csv").open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        fieldnames = [f for f in reader.fieldnames if f != "withdrawals"]
    with (tmp_path / "course_metrics.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            row.pop("withdrawals", None)
            writer.writerow(row)

    sources = load_approved_sources(tmp_path)
    with pytest.raises(HaltError):
        validate_schema(sources)


def test_missing_student_feedback_is_limitation_not_halt(tmp_path):
    for name in config.REQUIRED_FILES.values():
        shutil.copy(config.DATA_DIR / name, tmp_path / name)
    sources = load_approved_sources(tmp_path)
    validate_schema(sources)  # must not raise/halt
    assert missing_optional_files(sources) == ["student_feedback"]


def test_find_blank_required_values_flags_region_c():
    sources = load_approved_sources(config.DATA_DIR)
    issues = find_blank_required_values(sources)
    matches = [
        i
        for i in issues
        if i.file == "course_metrics.csv" and i.field == "withdrawals" and i.segment.get("delivery_region") == "Region C"
    ]
    assert len(matches) == 1
    assert matches[0].segment["reporting_period"] == "2025"


def test_reconcile_status_counts_passes_for_complete_rows():
    sources = load_approved_sources(config.DATA_DIR)
    rows = sources.get("course_metrics").rows
    blanks = find_blank_required_values(sources)
    excluded = {i.row_index for i in blanks if i.file == "course_metrics.csv"}
    issues = reconcile_status_counts(rows, excluded_row_indexes=excluded)
    assert issues == []


def test_reconcile_status_counts_fails_for_broken_copy():
    rows = [
        {
            "reporting_period": "2025",
            "delivery_region": "Region A",
            "delivery_mode": "Face to face",
            "enrolments": "430",
            "commencements": "310",
            "completions": "160",
            "in_progress": "170",
            "withdrawals": "50",  # deliberately wrong: should be 100
        }
    ]
    issues = reconcile_status_counts(rows)
    assert len(issues) == 1
    assert issues[0].enrolments == 430
    assert issues[0].sum_of_status_fields == 380


def test_check_satisfaction_sample_size_no_low_samples_in_real_data():
    sources = load_approved_sources(config.DATA_DIR)
    issues = check_satisfaction_sample_size(sources.get("satisfaction").rows)
    assert issues == []


def test_check_satisfaction_sample_size_flags_below_threshold():
    rows = [
        {"reporting_period": "2025", "delivery_mode": "Blended", "survey_item": "teacher_support", "n_respondents": "12"}
    ]
    issues = check_satisfaction_sample_size(rows)
    assert len(issues) == 1
    assert issues[0].n_respondents == 12


# --- plain-language summaries --------------------------------------------


def test_blank_value_summary_is_plain_language_not_technical():
    issue = BlankValueIssue(
        file="course_metrics.csv",
        row_index=0,
        field="withdrawals",
        segment={"reporting_period": "2025", "delivery_region": "Region C", "delivery_mode": "Blended"},
        issue_key="course_metrics.csv:2025:Region C:Blended:withdrawals",
    )
    summary = blank_value_summary(issue)
    assert summary == "Region C blended delivery was excluded because the 2025 withdrawal figure was missing."
    # No raw field/key=value technical syntax in the plain summary.
    assert "delivery_region=" not in summary
    assert "reporting_period=" not in summary


def test_reconciliation_summary_is_plain_language():
    issue = ReconciliationIssue(
        file="course_metrics.csv",
        row_index=0,
        segment={"reporting_period": "2025", "delivery_region": "Region C", "delivery_mode": "Blended"},
        enrolments=310,
        sum_of_status_fields=300,
        difference=-10,
        issue_key="course_metrics.csv:2025:Region C:Blended:reconciliation",
    )
    summary = reconciliation_summary(issue)
    assert "Region C blended delivery" in summary
    assert "2025" in summary
    assert "didn't add up" in summary
    assert "delivery_region=" not in summary


def test_low_sample_summaries_are_plain_language():
    issue = LowSampleIssue(
        file="satisfaction.csv",
        row_index=0,
        segment={"reporting_period": "2025", "delivery_mode": "Blended", "survey_item": "teacher_support"},
        n_respondents=12,
        issue_key="satisfaction.csv:2025:Blended:teacher_support:n_respondents",
    )
    excluded = low_sample_excluded_summary(issue)
    proceeded = low_sample_proceeded_summary(issue)
    assert "Blended teacher support results" in excluded
    assert "excluded" in excluded
    assert "Blended teacher support results" in proceeded
    assert "included with caution" in proceeded
    assert "survey_item=" not in excluded and "survey_item=" not in proceeded
