import json

from cpc_agent.evidence import EvidenceRecord
from cpc_agent.gates import Gates
from cpc_agent.screening import EligibleComment
from cpc_agent.validation import BlankValueIssue, LowSampleIssue


def _write_answers(tmp_path, data):
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class _Ctx:
    course_code = "CWS30125"
    course_title = "Test"
    current_period = "2025"
    previous_period = "2024"
    files_found = [("course_metrics", "course_metrics.csv", True)]


def test_scripted_scope_confirm(tmp_path):
    path = _write_answers(tmp_path, {"scope_confirm": True})
    gates = Gates(str(path))
    assert gates.confirm_scope(_Ctx()) is True


def test_scripted_scope_confirm_captures_analyst_name(tmp_path):
    path = _write_answers(tmp_path, {"scope_confirm": True, "analyst_name": "Farley Dahl"})
    gates = Gates(str(path))
    gates.confirm_scope(_Ctx())
    assert gates.analyst_name == "Farley Dahl"


def test_scripted_scope_confirm_defaults_analyst_name_when_missing(tmp_path):
    path = _write_answers(tmp_path, {"scope_confirm": True})
    gates = Gates(str(path))
    gates.confirm_scope(_Ctx())
    assert gates.analyst_name == "(not provided)"


def test_interactive_scope_confirm_prompts_for_name_and_confirmation(monkeypatch):
    answers = iter(["Farley Dahl", "y"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(answers))
    gates = Gates(answers_path=None)
    result = gates.confirm_scope(_Ctx())
    assert result is True
    assert gates.analyst_name == "Farley Dahl"


def test_analyst_name_logged_on_scope_confirm(tmp_path):
    from cpc_agent.audit import AuditLog

    audit = AuditLog("run-x", runs_dir=tmp_path)
    path = _write_answers(tmp_path, {"scope_confirm": True, "analyst_name": "Farley Dahl"})
    gates = Gates(str(path), audit=audit)
    gates.confirm_scope(_Ctx())
    audit.close()

    lines = (tmp_path / "run-x" / "audit_log.jsonl").read_text(encoding="utf-8").strip().splitlines()
    events = [json.loads(line) for line in lines]
    scope_event = next(e for e in events if e.get("gate") == "scope_confirm")
    assert scope_event["analyst"] == "Farley Dahl"


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


def test_review_findings_records_the_analyst_name_from_scope_gate(tmp_path):
    path = _write_answers(
        tmp_path, {"scope_confirm": True, "analyst_name": "Farley Dahl", "findings_review": {"default": "accept"}}
    )
    gates = Gates(str(path))
    gates.confirm_scope(_Ctx())
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
    assert reviewed[0].reviewer == "Farley Dahl"


def test_interactive_review_findings_shows_full_eligible_comment_text(monkeypatch, capsys):
    gates = Gates(answers_path=None)
    gates.analyst_name = "Farley Dahl"
    record = EvidenceRecord(
        finding_id="S1",
        measure="teacher_support",
        segment="Online",
        calculation="61% (n=380)",
        comparison_rule="rule",
        source_file="satisfaction.csv",
        period="2025",
        n=380,
        aligned_ids=["FB003", "FB016"],  # FB016 deliberately not in comments_by_id (simulates a quarantined id)
        countervailing_ids=["FB005"],
    )
    comments_by_id = {
        "FB003": EligibleComment(
            comment_id="FB003", reporting_period="2025", delivery_region="Online", delivery_mode="Online",
            prompt="Needs improvement", text="I emailed my trainer three times and waited over a week for a reply.",
        ),
        "FB005": EligibleComment(
            comment_id="FB005", reporting_period="2025", delivery_region="Online", delivery_mode="Online",
            prompt="Best aspect", text="My online teacher always replied the same day.",
        ),
    }
    monkeypatch.setattr("builtins.input", lambda _prompt="": "accept")

    gates.review_findings([record], comments_by_id)

    captured = capsys.readouterr().out
    assert "I emailed my trainer three times and waited over a week for a reply." in captured
    assert "My online teacher always replied the same day." in captured
    # FB016 has no entry in comments_by_id (i.e. it was never eligible) —
    # its text must never be shown, only a placeholder.
    assert "(comment text unavailable)" in captured
    assert "Hughes" not in captured
    # Readable label, not the raw code name, is shown in the header line.
    assert "Teacher support satisfaction (Online)" in captured


def test_scripted_final_approval(tmp_path):
    path = _write_answers(tmp_path, {"final_approval": "approve"})
    gates = Gates(str(path))
    assert gates.final_approval("runs/x/brief.md") == "approve"


def test_scripted_final_approval_invalid_value_defaults_to_return(tmp_path):
    path = _write_answers(tmp_path, {"final_approval": "yolo"})
    gates = Gates(str(path))
    assert gates.final_approval("runs/x/brief.md") == "return"
