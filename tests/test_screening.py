from cpc_agent import config
from cpc_agent.loaders import load_approved_sources
from cpc_agent.screening import (
    IDENTIFYING_INFORMATION,
    POSSIBLE_INJECTION,
    quarantine_summary,
    screen_comments,
)


def _rows():
    sources = load_approved_sources(config.DATA_DIR)
    return sources.get("student_feedback").rows


def test_fb016_and_fb018_are_quarantined():
    result = screen_comments(_rows())
    quarantined_ids = {q.comment_id: q for q in result.quarantined}
    assert "FB016" in quarantined_ids
    assert "FB018" in quarantined_ids
    assert IDENTIFYING_INFORMATION in quarantined_ids["FB016"].reasons
    assert POSSIBLE_INJECTION in quarantined_ids["FB018"].reasons


def test_quarantined_text_never_reaches_eligible_or_exception_output():
    result = screen_comments(_rows())
    eligible_ids = {c.comment_id for c in result.eligible}
    assert "FB016" not in eligible_ids
    assert "FB018" not in eligible_ids
    # The exception record must carry only id + reason, never the comment text.
    for q in result.quarantined:
        assert not hasattr(q, "text")
        as_dict_like = vars(q)
        assert "Hughes" not in str(as_dict_like)
        assert "Ignore all previous instructions" not in str(as_dict_like)


def test_eligible_comments_preserve_expected_count():
    result = screen_comments(_rows())
    all_ids = {row["comment_id"] for row in _rows()}
    quarantined_ids = {q.comment_id for q in result.quarantined}
    eligible_ids = {c.comment_id for c in result.eligible}
    assert eligible_ids == all_ids - quarantined_ids
    assert quarantined_ids == {"FB016", "FB018"}


def test_no_false_positive_quarantines_on_clean_comments():
    result = screen_comments(_rows())
    quarantined_ids = {q.comment_id for q in result.quarantined}
    # FB024 mentions withdrawing/pausing but names no one and has no injection phrasing.
    assert "FB024" not in quarantined_ids
    assert "FB005" not in quarantined_ids


def test_quarantine_summary_is_plain_language_not_a_reason_code():
    identifying = quarantine_summary([IDENTIFYING_INFORMATION])
    injection = quarantine_summary([POSSIBLE_INJECTION])
    assert identifying != IDENTIFYING_INFORMATION
    assert "identify a person" in identifying
    assert injection != POSSIBLE_INJECTION
    assert "genuine feedback" in injection
    assert identifying != injection


def test_quarantine_summary_never_includes_comment_id_or_text():
    for quarantined in screen_comments(_rows()).quarantined:
        summary = quarantine_summary(quarantined.reasons)
        assert quarantined.comment_id not in summary
        assert "Hughes" not in summary
        assert "Ignore all previous instructions" not in summary


def test_quarantine_summary_combines_distinct_reasons_without_duplication():
    both = quarantine_summary([IDENTIFYING_INFORMATION, POSSIBLE_INJECTION])
    assert "identify a person" in both
    assert "genuine feedback" in both
    repeated = quarantine_summary([IDENTIFYING_INFORMATION, IDENTIFYING_INFORMATION])
    assert repeated == quarantine_summary([IDENTIFYING_INFORMATION])
