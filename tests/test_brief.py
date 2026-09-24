from cpc_agent import config
from cpc_agent.brief import populate_cpc_template, render_brief_html, update_brief_status
from cpc_agent.evidence import EvidenceRecord
from cpc_agent.screening import EligibleComment
from cpc_agent.validation import ScopeContext

FORBIDDEN_STRINGS = [
    "Daniel Hughes",
    "Hughes",
    "Ignore all previous instructions",
    "poor performing",
    "caused by",
]


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
        value=61.0,
        details={"organisation_avg_pct": 72.0, "prev_pct_satisfied": 66.0},
        aligned_ids=["FB003", "FB007", "FB011"],
        countervailing_ids=["FB005"],
        aligned_excerpt_ids=["FB003", "FB007"],
        countervailing_excerpt_ids=["FB005"],
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
        value=1.0,
        aligned_ids=["FB999"],
        aligned_excerpt_ids=["FB999"],
    )
    r.reviewer_status = "reject"
    return r


def _comments_by_id():
    return {
        "FB003": EligibleComment(
            comment_id="FB003", reporting_period="2025", delivery_region="Online", delivery_mode="Online",
            prompt="Needs improvement", text="I emailed my trainer three times and waited over a week for a reply.",
        ),
        "FB007": EligibleComment(
            comment_id="FB007", reporting_period="2025", delivery_region="Online", delivery_mode="Online",
            prompt="Needs improvement", text="Hard to get help when you study online at night.",
        ),
        "FB011": EligibleComment(
            comment_id="FB011", reporting_period="2025", delivery_region="Online", delivery_mode="Online",
            prompt="Needs improvement", text="Teachers seem stretched thin.",
        ),
        "FB005": EligibleComment(
            comment_id="FB005", reporting_period="2025", delivery_region="Online", delivery_mode="Online",
            prompt="Best aspect", text="My online teacher always replied the same day.",
        ),
        # FB999 is deliberately absent: simulates an id that slipped past
        # validation and is not eligible/known — must never render as text.
    }


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


# --- brief.html --------------------------------------------------------


def _render_html(tmp_path, accepted=None, exceptions=None, decision="approve", analyst_name="Farley Dahl"):
    return render_brief_html(
        accepted_records=accepted if accepted is not None else [_accepted_record()],
        context=_context(),
        run_id="run-1",
        data_version="abc123",
        generated_date="2026-09-24",
        snapshot_date="2025-12-31",
        exceptions=exceptions if exceptions is not None else {"exclusions": [], "quarantined": [], "limitations": []},
        analyst_name=analyst_name,
        decision=decision,
        comments_by_id=_comments_by_id(),
        output_dir=tmp_path,
    )


def test_render_brief_html_writes_file_with_run_data(tmp_path):
    path = _render_html(tmp_path)
    assert path.name == "brief.html"
    text = path.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in text
    assert "CWS30125" in text
    assert "S1" in text
    assert "Run run-1" in text
    assert "data abc123" in text
    assert "Farley Dahl" in text


def test_render_brief_html_uses_packaged_css_verbatim(tmp_path):
    path = _render_html(tmp_path)
    text = path.read_text(encoding="utf-8")
    css_text = config.BRIEF_CSS_PATH.read_text(encoding="utf-8")
    assert f"<style>\n{css_text}</style>" in text


def test_render_brief_html_does_not_depend_on_docs_directory(tmp_path):
    # The default CSS source lives inside the installed package, not docs/.
    assert config.BRIEF_CSS_PATH.is_relative_to(config.PACKAGE_DIR)
    assert not config.BRIEF_CSS_PATH.is_relative_to(config.BASE_DIR / "docs")

    mockup_path = config.BASE_DIR / "docs" / "brief_mockup.html"
    moved_aside = mockup_path.with_suffix(".html.movedfortest")
    mockup_path.rename(moved_aside)
    try:
        # render_brief_html still succeeds with docs/brief_mockup.html absent,
        # using its default css_path (the packaged stylesheet).
        path = _render_html(tmp_path)
        assert path.exists()
        assert "<style>" in path.read_text(encoding="utf-8")
    finally:
        moved_aside.rename(mockup_path)


def test_render_brief_html_contains_only_accepted_records(tmp_path):
    # _rejected_record (S9) is deliberately not in accepted_records.
    path = _render_html(tmp_path, accepted=[_accepted_record()])
    text = path.read_text(encoding="utf-8")
    assert "S9" not in text
    assert "FB999" not in text


def test_render_brief_html_contains_no_forbidden_strings(tmp_path):
    path = _render_html(tmp_path)
    text = path.read_text(encoding="utf-8")
    for term in FORBIDDEN_STRINGS:
        assert term not in text


def test_render_brief_html_never_shows_text_for_an_unknown_or_quarantined_id(tmp_path):
    record = _accepted_record()
    # FB016 is not in comments_by_id at all (simulates a quarantined id) —
    # deliberately add it to aligned_ids/excerpts as a defence-in-depth check.
    record.aligned_ids = record.aligned_ids + ["FB016"]
    record.aligned_excerpt_ids = ["FB016"]
    path = _render_html(tmp_path, accepted=[record])
    text = path.read_text(encoding="utf-8")
    assert "(comment text unavailable)" in text
    assert "Hughes" not in text
    assert "Mr Daniel Hughes" not in text


def test_render_brief_html_shows_at_most_two_excerpts_per_side(tmp_path):
    # aligned_ids has three linked comments, but only two are excerpt ids.
    path = _render_html(tmp_path)
    text = path.read_text(encoding="utf-8")
    assert text.count("<blockquote") == 3  # 2 aligned excerpts + 1 countervailing excerpt
    assert "Comments consistent with this result (3)" in text  # full linked count, not excerpt count
    assert "I emailed my trainer three times" in text
    assert "Hard to get help when you study online" in text
    # FB011 is linked (counted) but not chosen as an excerpt — its text must not appear.
    assert "Teachers seem stretched thin" not in text


def test_render_brief_html_respects_analyst_chosen_excerpts_not_just_first_two(tmp_path):
    record = _accepted_record()
    record.aligned_excerpt_ids = ["FB007", "FB011"]  # analyst chose these, not the first two
    path = _render_html(tmp_path, accepted=[record])
    text = path.read_text(encoding="utf-8")
    assert "Hard to get help when you study online" in text
    assert "Teachers seem stretched thin" in text
    assert "I emailed my trainer three times" not in text


def test_render_brief_html_gauge_values_are_data_driven_not_hardcoded(tmp_path):
    record = _accepted_record()  # value=61.0, prev=66.0, org_avg=72.0
    path = _render_html(tmp_path, accepted=[record])
    text = path.read_text(encoding="utf-8")
    assert 'style="width:61%"' in text
    assert 'left:66%' in text
    assert '2024: 66%' in text
    assert 'left:72%' in text
    assert 'avg 72%' in text

    # A different record's values must produce different gauge numbers —
    # proves these are computed, not copy-pasted from the mockup.
    other = _accepted_record()
    other.finding_id = "S2"
    other.value = 34.0
    other.details = {"median_unit_withdrawal_rate": 12.49}
    other.measure = "unit_withdrawal_rate"
    other.segment = "CWSU204"
    other.aligned_ids = []
    other.aligned_excerpt_ids = []
    other.countervailing_ids = []
    other.countervailing_excerpt_ids = []
    path2 = _render_html(tmp_path, accepted=[other])
    text2 = path2.read_text(encoding="utf-8")
    assert 'style="width:34%"' in text2
    assert "typical unit: 12.5%" in text2
    assert 'style="width:61%"' not in text2


def test_render_brief_html_no_comment_evidence_message_when_nothing_linked(tmp_path):
    record = _accepted_record()
    record.aligned_ids = []
    record.aligned_excerpt_ids = []
    record.countervailing_ids = []
    record.countervailing_excerpt_ids = []
    path = _render_html(tmp_path, accepted=[record])
    text = path.read_text(encoding="utf-8")
    assert "No comment evidence" in text
    assert "No student comments relate to this finding" in text
    assert "<blockquote" not in text


def test_render_brief_html_readable_label_and_question(tmp_path):
    path = _render_html(tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "Teacher support satisfaction" in text
    assert "teacher_support" not in text  # raw code name must not leak into the HTML
    assert "For discussion:" in text  # from the CSS ::before, backed by a real .question div
    assert "What support or delivery conditions" in text


def test_render_brief_html_approve_vs_return_wording(tmp_path):
    approved_text = _render_html(tmp_path, decision="approve").read_text(encoding="utf-8")
    assert "Approved for the workshop by" in approved_text

    returned_path = render_brief_html(
        accepted_records=[_accepted_record()],
        context=_context(),
        run_id="run-2",
        data_version="abc123",
        generated_date="2026-09-24",
        snapshot_date="2025-12-31",
        exceptions={"exclusions": [], "quarantined": [], "limitations": []},
        analyst_name="Farley Dahl",
        decision="return",
        comments_by_id=_comments_by_id(),
        output_dir=tmp_path / "returned",
    )
    returned_text = returned_path.read_text(encoding="utf-8")
    assert "Returned for revision by" in returned_text
    assert "Approved for the workshop by" not in returned_text


def test_render_brief_html_exceptions_show_plain_language_summary_not_technical_detail(tmp_path):
    exceptions = {
        "exclusions": [
            {
                "issue_key": "course_metrics.csv:2025:Region C:Blended:withdrawals",
                "detail": "Excluded reporting_period=2025, delivery_region=Region C, delivery_mode=Blended from course_metrics.csv — blank 'withdrawals' (value never estimated).",
                "summary": "Region C blended delivery was excluded because the 2025 withdrawal figure was missing.",
            }
        ],
        "quarantined": [
            {
                "comment_id": "FB016",
                "reason": "identifying_information",
                "detail": "Quarantined comment FB016: identifying_information.",
                "summary": "A comment was held back from review because it appeared to identify a person.",
            }
        ],
        "limitations": [],
    }
    path = _render_html(tmp_path, exceptions=exceptions)
    text = path.read_text(encoding="utf-8")
    # Plain-language summaries are shown...
    assert "Region C blended delivery was excluded because the 2025 withdrawal figure was missing." in text
    assert "A comment was held back from review because it appeared to identify a person." in text
    # ...but the technical detail string (field names, key=value pairs, comment IDs) is not —
    # that stays in exception_log.json only.
    assert "reporting_period=2025" not in text
    assert "delivery_region=Region C" not in text
    assert "FB016" not in text
    assert "Hughes" not in text


def test_populate_cpc_template_shows_plain_language_summary_not_technical_detail(tmp_path):
    exceptions = {
        "exclusions": [
            {
                "issue_key": "x",
                "detail": "Excluded reporting_period=2025, delivery_region=Region C, delivery_mode=Blended from course_metrics.csv — blank 'withdrawals' (value never estimated).",
                "summary": "Region C blended delivery was excluded because the 2025 withdrawal figure was missing.",
            }
        ],
        "quarantined": [],
        "limitations": [],
    }
    path = populate_cpc_template(
        accepted_records=[_accepted_record()],
        context=_context(),
        run_id="run-1",
        data_version="abc123",
        instruction_version="1.0.0",
        generated_date="2026-09-24",
        snapshot_date="2025-12-31",
        exceptions=exceptions,
        output_dir=tmp_path,
    )
    text = path.read_text(encoding="utf-8")
    assert "Region C blended delivery was excluded because the 2025 withdrawal figure was missing." in text
    assert "reporting_region=Region C" not in text
    assert "delivery_region=Region C" not in text


def test_render_brief_html_has_no_external_resources(tmp_path):
    path = _render_html(tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "googleapis" not in text
    assert "fonts.google" not in text
    assert "<link" not in text
    assert "http://" not in text
    assert "https://" not in text
    # Only system font stacks, never the removed webfont family names.
    assert "Public Sans" not in text
    assert "Source Serif 4" not in text
    assert "Segoe UI" in text
    assert "Georgia" in text


def test_render_brief_html_shows_automatic_excerpt_selection_label(tmp_path):
    record = _accepted_record()  # excerpt fields default to automatic selection
    path = _render_html(tmp_path, accepted=[record])
    text = path.read_text(encoding="utf-8")
    assert "Excerpts: automatic selection (first two by ID)." in text


def test_render_brief_html_shows_analyst_excerpt_selection_label(tmp_path):
    record = _accepted_record()
    record.aligned_excerpt_selection = "analyst selection"
    path = _render_html(tmp_path, accepted=[record])
    text = path.read_text(encoding="utf-8")
    assert "Excerpts: analyst selection." in text
    # The countervailing side wasn't touched by the analyst in this record's fixture default.
    assert "Excerpts: automatic selection (first two by ID)." in text
