import json

from cpc_agent.evidence import EvidenceRecord
from cpc_agent.gates import Gates
from cpc_agent.validation import BlankValueIssue, LowSampleIssue


def _write_answers(tmp_path, data):
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_scripted_scope_confirm(tmp_path):
    path = _write_answers(tmp_path, {"scope_confirm": True})
    gates = Gates(str(path))

    class Ctx:
        course_code = "CWS30125"
        course_title = "Test"
        current_period = "2025"
        previous_period = "2024"
        files_found = [("course_metrics", "course_metrics.csv", True)]

    assert gates.confirm_scope(Ctx()) is True


def test_scripted_blank_value_default_and_specific_key(tmp_path):
    path = _write_answers(
        tmp_path,
        {"blank_value": {"course_metrics.csv:2025:Region C:Blended:withdrawals": "exclude", "default": "abort"}},
    )
    gates = Gates(str(path))
    matched = BlankValueIssue(
        file="course_metrics.csv",
        row_index=0,
        field="withdrawals",
        segment={"reporting_period": "2025", "delivery_region": "Region C", "delivery_mode": "Blended"},
        issue_key="course_metrics.csv:2025:Region C:Blended:withdrawals",
    )
    assert gates.resolve_blank_value(matched) == "exclude"

    other = BlankValueIssue(
        file="course_metrics.csv",
        row_index=1,
        field="enrolments",
        segment={"reporting_period": "2025", "delivery_region": "Region X", "delivery_mode": "Online"},
        issue_key="course_metrics.csv:2025:Region X:Online:enrolments",
    )
    assert gates.resolve_blank_value(other) == "abort"


def test_scripted_low_sample_default(tmp_path):
    path = _write_answers(tmp_path, {"low_sample": {"default": "proceed"}})
    gates = Gates(str(path))
    issue = LowSampleIssue(
        file="satisfaction.csv",
        row_index=0,
        segment={"reporting_period": "2025", "delivery_mode": "Blended", "survey_item": "teacher_support"},
        n_respondents=12,
        issue_key="satisfaction.csv:2025:Blended:teacher_support:n_respondents",
    )
    assert gates.resolve_low_sample(issue) == "proceed"


def test_scripted_ask_analyst_default_answer(tmp_path):
    path = _write_answers(tmp_path, {"ask_analyst": {"default": "Use recorded evidence only."}})
    gates = Gates(str(path))
    assert gates.ask_analyst("Is this a withdrawal or just a scare?") == "Use recorded evidence only."


def test_scripted_findings_review_accepts_all(tmp_path):
    path = _write_answers(tmp_path, {"findings_review": {"default": "accept"}})
    gates = Gates(str(path))
    record = EvidenceRecord(
        finding_id="S1",
        measure="teacher_support",
        segment="Online",
        calculation="61% (n=380)",
        comparison_rule="rule",
        source_file="satisfaction.csv",
        period="2025",
        n=380,
    )
    reviewed = gates.review_findings([record])
    assert reviewed[0].reviewer_status == "accept"


def test_scripted_final_approval(tmp_path):
    path = _write_answers(tmp_path, {"final_approval": "approve"})
    gates = Gates(str(path))
    assert gates.final_approval("runs/x/brief.md") == "approve"


def test_scripted_final_approval_invalid_value_defaults_to_return(tmp_path):
    path = _write_answers(tmp_path, {"final_approval": "yolo"})
    gates = Gates(str(path))
    assert gates.final_approval("runs/x/brief.md") == "return"
