from cpc_agent import config
from cpc_agent.brief import populate_cpc_template, update_brief_status
from cpc_agent.evidence import EvidenceRecord
from cpc_agent.validation import ScopeContext


def _context():
    return ScopeContext(
        course_code=config.COURSE_CODE,
        course_title=config.COURSE_TITLE,
        current_period="2025",
        previous_period="2024",
        files_found=[
            ("course_metrics", "course_metrics.csv", True),
            ("satisfaction", "satisfaction.csv", True),
            ("unit_results", "unit_results.csv", True),
            ("student_feedback", "student_feedback.csv", True),
        ],
    )


def _accepted_record():
    r = EvidenceRecord(
        finding_id="S1",
        measure="teacher_support",
        segment="Online",
        calculation="61% satisfied (n=380)",
        comparison_rule="11 points below organisation average (72%, 2025)",
        source_file="satisfaction.csv",
        period="2025",
        n=380,
        aligned_ids=["FB003", "FB007"],
        countervailing_ids=["FB005"],
        themes=["Teacher responsiveness and support"],
        evidence_status="Mixed",
        limitation="Aligned and countervailing feedback were both found.",
    )
    r.reviewer_status = "accept"
    return r


def _rejected_record():
    r = EvidenceRecord(
        finding_id="S9",
        measure="assessment_clarity",
        segment="Online",
        calculation="1% satisfied (n=380)",
        comparison_rule="test",
        source_file="satisfaction.csv",
        period="2025",
        n=380,
    )
    r.reviewer_status = "reject"
    return r


def test_populate_cpc_template_writes_brief(tmp_path):
    accepted = [_accepted_record()]
    exceptions = {
        "exclusions": [{"issue_key": "x", "detail": "Excluded Region C from course_metrics.csv."}],
        "quarantined": [{"comment_id": "FB016", "reason": "identifying_information", "detail": "Quarantined comment FB016: identifying_information."}],
        "limitations": [],
    }
    path = populate_cpc_template(
        accepted_records=accepted,
        context=_context(),
        run_id="run-1",
        data_version="abc123",
        instruction_version="1.0.0",
        generated_date="2026-09-24",
        snapshot_date="2025-12-31",
        exceptions=exceptions,
        output_dir=tmp_path,
    )
    assert path.name == "brief.md"
    text = path.read_text(encoding="utf-8")
    assert "S1" in text
    assert "Run ID: run-1" in text
    assert "Input data version: abc123" in text
    assert "Instruction version: 1.0.0" in text
    assert "FB003, FB007" in text or "FB003" in text
    assert "Quarantined comment FB016" in text
    assert "Status: Pending final approval" in text


def test_brief_contains_only_accepted_records(tmp_path):
    accepted = [_accepted_record()]  # rejected record deliberately not passed in
    path = populate_cpc_template(
        accepted_records=accepted,
        context=_context(),
        run_id="run-1",
        data_version="abc123",
        instruction_version="1.0.0",
        generated_date="2026-09-24",
        snapshot_date="2025-12-31",
        exceptions={"exclusions": [], "quarantined": [], "limitations": []},
        output_dir=tmp_path,
    )
    text = path.read_text(encoding="utf-8")
    assert "S9" not in text


def test_brief_contains_no_forbidden_strings(tmp_path):
    forbidden = [
        "Daniel Hughes",
        "Hughes",
        "Ignore all previous instructions",
        "poor performing",
        "caused by",
    ]
    accepted = [_accepted_record()]
    path = populate_cpc_template(
        accepted_records=accepted,
        context=_context(),
        run_id="run-1",
        data_version="abc123",
        instruction_version="1.0.0",
        generated_date="2026-09-24",
        snapshot_date="2025-12-31",
        exceptions={"exclusions": [], "quarantined": [], "limitations": []},
        output_dir=tmp_path,
    )
    text = path.read_text(encoding="utf-8")
    for term in forbidden:
        assert term not in text


def test_update_brief_status_rewrites_status_and_decision(tmp_path):
    accepted = [_accepted_record()]
    path = populate_cpc_template(
        accepted_records=accepted,
        context=_context(),
        run_id="run-1",
        data_version="abc123",
        instruction_version="1.0.0",
        generated_date="2026-09-24",
        snapshot_date="2025-12-31",
        exceptions={"exclusions": [], "quarantined": [], "limitations": []},
        output_dir=tmp_path,
    )
    update_brief_status(path, "approve")
    text = path.read_text(encoding="utf-8")
    assert "Status: Approved" in text
    assert "Decision: approve" in text


def test_populate_cpc_template_records_analyst_name(tmp_path):
    path = populate_cpc_template(
        accepted_records=[_accepted_record()],
        context=_context(),
        run_id="run-1",
        data_version="abc123",
        instruction_version="1.0.0",
        generated_date="2026-09-24",
        snapshot_date="2025-12-31",
        exceptions={"exclusions": [], "quarantined": [], "limitations": []},
        output_dir=tmp_path,
        analyst_name="Farley Dahl",
    )
    text = path.read_text(encoding="utf-8")
    assert "Analyst: Farley Dahl" in text


def test_populate_cpc_template_defaults_analyst_name_when_not_given(tmp_path):
    path = populate_cpc_template(
        accepted_records=[_accepted_record()],
        context=_context(),
        run_id="run-1",
        data_version="abc123",
        instruction_version="1.0.0",
        generated_date="2026-09-24",
        snapshot_date="2025-12-31",
        exceptions={"exclusions": [], "quarantined": [], "limitations": []},
        output_dir=tmp_path,
    )
    text = path.read_text(encoding="utf-8")
    assert "Analyst: (not provided)" in text


def test_populate_cpc_template_removes_bracket_placeholder_lines(tmp_path):
    path = populate_cpc_template(
        accepted_records=[_accepted_record()],
        context=_context(),
        run_id="run-1",
        data_version="abc123",
        instruction_version="1.0.0",
        generated_date="2026-09-24",
        snapshot_date="2025-12-31",
        exceptions={
            "exclusions": [{"issue_key": "x", "detail": "Excluded Region C from course_metrics.csv."}],
            "quarantined": [],
            "limitations": [],
        },
        output_dir=tmp_path,
        analyst_name="Farley Dahl",
    )
    text = path.read_text(encoding="utf-8")
    placeholder_lines = [
        "[code and title]",
        "[Files used, periods, any sources unavailable]",
        "[Only findings the analyst has approved. Detailed evidence records are kept separately.]",
        "[Missing data, quarantined comments, low samples, unresolved issues]",
        "[Neutral questions linked to signal IDs. No recommendations.]",
    ]
    for placeholder in placeholder_lines:
        assert placeholder not in text
    # The real content that replaced each placeholder is still present.
    assert "Files used: course_metrics.csv" in text
    assert "Excluded Region C from course_metrics.csv." in text
    assert "What support or delivery conditions" in text


def test_populate_cpc_template_uses_readable_measure_labels(tmp_path):
    path = populate_cpc_template(
        accepted_records=[_accepted_record()],
        context=_context(),
        run_id="run-1",
        data_version="abc123",
        instruction_version="1.0.0",
        generated_date="2026-09-24",
        snapshot_date="2025-12-31",
        exceptions={"exclusions": [], "quarantined": [], "limitations": []},
        output_dir=tmp_path,
    )
    text = path.read_text(encoding="utf-8")
    assert "Teacher support satisfaction (Online)" in text
    assert "teacher_support (Online)" not in text  # raw code name must not appear in the brief


def test_evidence_records_json_keeps_code_names_even_when_brief_is_readable():
    record = _accepted_record()
    # The dataclass (and therefore evidence_records.json via to_dict()) always
    # keeps the raw code name — only brief.py's rendering is humanized.
    assert record.measure == "teacher_support"
    assert record.to_dict()["measure"] == "teacher_support"


def test_update_brief_status_approve_removes_draft_and_not_approved(tmp_path):
    path = populate_cpc_template(
        accepted_records=[_accepted_record()],
        context=_context(),
        run_id="run-1",
        data_version="abc123",
        instruction_version="1.0.0",
        generated_date="2026-09-24",
        snapshot_date="2025-12-31",
        exceptions={"exclusions": [], "quarantined": [], "limitations": []},
        output_dir=tmp_path,
    )
    assert "DRAFT" in path.read_text(encoding="utf-8")  # sanity check on the unmodified draft

    update_brief_status(path, "approve")
    text = path.read_text(encoding="utf-8")
    assert "DRAFT" not in text
    assert "(not approved)" not in text
    assert "# Course Performance Workshop Brief" in text
    assert "Status: Approved" in text


def test_update_brief_status_return_keeps_draft_marker_and_shows_returned(tmp_path):
    path = populate_cpc_template(
        accepted_records=[_accepted_record()],
        context=_context(),
        run_id="run-1",
        data_version="abc123",
        instruction_version="1.0.0",
        generated_date="2026-09-24",
        snapshot_date="2025-12-31",
        exceptions={"exclusions": [], "quarantined": [], "limitations": []},
        output_dir=tmp_path,
    )
    update_brief_status(path, "return")
    text = path.read_text(encoding="utf-8")
    assert "DRAFT" in text
    assert "(not approved)" in text
    assert "Status: Returned" in text
    assert "Decision: return" in text
