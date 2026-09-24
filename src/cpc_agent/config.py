"""Paths and thresholds for the CPC Preparation Agent.

Values here mirror the rules written in `data/data_dictionary.txt`. If the
dictionary changes, update this file and bump INSTRUCTION_VERSION.
"""
from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
RUNS_DIR = BASE_DIR / "runs"
TEMPLATE_PATH = DATA_DIR / "cpc_brief_template.txt"

COURSE_CODE = "CWS30125"
COURSE_TITLE = "Certificate III in Community Wellbeing Support"
CURRENT_PERIOD = "2025"
PREVIOUS_PERIOD = "2024"

# Instruction/rules version. Bump when the rules mirrored from
# data_dictionary.txt (thresholds, evidence-status order, tool contracts)
# change, so audit logs and briefs record which rule set produced them.
INSTRUCTION_VERSION = "1.0.0"

# --- Source files -----------------------------------------------------

REQUIRED_FILES: dict[str, str] = {
    "course_metrics": "course_metrics.csv",
    "satisfaction": "satisfaction.csv",
    "unit_results": "unit_results.csv",
}
OPTIONAL_FILES: dict[str, str] = {
    "student_feedback": "student_feedback.csv",
}
ALL_FILES: dict[str, str] = {**REQUIRED_FILES, **OPTIONAL_FILES}

REQUIRED_COLUMNS: dict[str, list[str]] = {
    "course_metrics.csv": [
        "reporting_period",
        "snapshot_date",
        "delivery_region",
        "delivery_mode",
        "enrolments",
        "commencements",
        "completions",
        "in_progress",
        "withdrawals",
    ],
    "satisfaction.csv": [
        "reporting_period",
        "delivery_mode",
        "survey_item",
        "pct_satisfied",
        "n_respondents",
        "organisation_avg_pct",
    ],
    "unit_results.csv": [
        "reporting_period",
        "unit_code",
        "unit_title",
        "enrolments",
        "completions",
        "withdrawals",
    ],
    "student_feedback.csv": [
        "comment_id",
        "reporting_period",
        "delivery_region",
        "delivery_mode",
        "prompt",
        "comment",
    ],
}

# Numeric fields that must not be blank in any row. organisation_avg_pct is
# deliberately excluded: the data model only reports it from 2025 onward, so
# a blank value in earlier rows is expected, not a data fault. See
# docs/decisions.md.
REQUIRED_VALUE_FIELDS: dict[str, list[str]] = {
    "course_metrics.csv": [
        "enrolments",
        "commencements",
        "completions",
        "in_progress",
        "withdrawals",
    ],
    "satisfaction.csv": ["pct_satisfied", "n_respondents"],
    "unit_results.csv": ["enrolments", "completions", "withdrawals"],
}

# Fields identifying a course_metrics.csv row's segment/snapshot, used to key
# exclusions and to keep excluded segments out of affected comparisons.
COURSE_METRICS_SEGMENT_KEYS = ["reporting_period", "delivery_region", "delivery_mode"]
RECONCILIATION_FIELDS = ["completions", "in_progress", "withdrawals"]
RECONCILIATION_TOTAL_FIELD = "enrolments"

# --- Thresholds (data_dictionary.txt "Flagging rules") -----------------

SATISFACTION_MIN_N = 30
SATISFACTION_GAP_VS_ORG_PP = 5
SATISFACTION_GAP_VS_PREV_PP = 5
SEGMENT_WITHDRAWAL_RISE_PP = 5
UNIT_WITHDRAWAL_RATE_MIN_PCT = 20.0
UNIT_WITHDRAWAL_MEDIAN_MULTIPLIER = 1.5

# --- Evidence status (data_dictionary.txt "Evidence status", in order) -

EVIDENCE_STATUS_ORDER = [
    "Review required",
    "Mixed",
    "Sufficient",
    "Limited",
    "No qualitative evidence",
]
EVIDENCE_SUFFICIENT_MIN_ALIGNED = 3
EVIDENCE_LIMITED_MIN_ALIGNED = 1

# --- Feedback themes (data_dictionary.txt "Feedback themes") -----------

THEMES = [
    "Teacher responsiveness and support",
    "Assessment clarity",
    "Assessment workload and timing",
    "Flexibility and course pacing",
    "Digital platform",
    "Practical learning and placement",
    "Facilities",
    "General/unclassifiable",
]

# --- Investigation loop --------------------------------------------------

MAX_TOOL_CALLS = 25
