import json

import pytest

from cpc_agent import config
from cpc_agent.investigate import (
    MAX_SEARCHES_PER_DIRECTION,
    MAX_SEARCHES_PER_RUN,
    MAX_SEARCH_RESULTS,
    InvestigationTools,
    ToolError,
    run_investigation,
)
from cpc_agent.loaders import load_approved_sources
from cpc_agent.metrics import Signal, calculate_metrics, flag_signals
from cpc_agent.screening import EligibleComment, screen_comments

REGION_C_EXCLUDED = {("2025", "Region C", "Blended")}


def _signals_and_comments():
    sources = load_approved_sources(config.DATA_DIR)
    bundle = calculate_metrics(
        sources.get("course_metrics").rows,
        sources.get("satisfaction").rows,
        sources.get("unit_results").rows,
        excluded_course_segment_keys=REGION_C_EXCLUDED,
    )
    signals = flag_signals(bundle)
    screening = screen_comments(sources.get("student_feedback").rows)
    return signals, screening.eligible


def _one_signal() -> Signal:
    return Signal(
        id="S1",
        kind="satisfaction",
        measure="teacher_support",
        segment="Online",
        value=61.0,
        n=380,
        calculation="61% satisfied (n=380)",
        comparison_rule="11 points below organisation average",
        source_file="satisfaction.csv",
        period="2025",
        sort_key=-11.0,
        details={},
    )


def _two_comments() -> list[EligibleComment]:
    return [
        EligibleComment(
            comment_id="FB003", reporting_period="2025", delivery_region="Online", delivery_mode="Online",
            prompt="Needs improvement", text="I waited a week for a reply to my question.",
        ),
        EligibleComment(
            comment_id="FB005", reporting_period="2025", delivery_region="Online", delivery_mode="Online",
            prompt="Best aspect", text="My teacher always replied the same day.",
        ),
    ]


# --- InvestigationTools (deterministic, no API) --------------------------


def test_list_signals_reports_all_flagged_signals():
    signals, comments = _signals_and_comments()
    tools = InvestigationTools(signals=signals, eligible_comments=comments, ask_analyst_fn=lambda q: "n/a")
    listed = tools.list_signals()
    assert {s["id"] for s in listed} == {"S1", "S2", "S3", "S4"}


def test_get_comment_corpus_profile_returns_counts_only():
    signals, comments = _signals_and_comments()
    tools = InvestigationTools(signals=signals, eligible_comments=comments, ask_analyst_fn=lambda q: "n/a")
    profile = tools.get_comment_corpus_profile()
    assert profile["total_eligible_comments"] == len(comments)
    assert set(profile["by_delivery_mode"]) <= {"Online", "Face to face", "Blended"}
    assert sum(profile["by_delivery_mode"].values()) == len(comments)
    assert sum(profile["by_delivery_region"].values()) == len(comments)
    assert sum(profile["by_reporting_period"].values()) == len(comments)
    # No comment text or IDs anywhere in the profile — counts only.
    blob = json.dumps(profile)
    assert "FB0" not in blob  # no comment_id-shaped strings
    for c in comments:
        assert c.text not in blob


def test_search_comments_requires_signal_id_direction_and_reason():
    signals, comments = _signals_and_comments()
    tools = InvestigationTools(signals=signals, eligible_comments=comments, ask_analyst_fn=lambda q: "n/a")
    with pytest.raises(ToolError):
        tools.search_comments(signal_id="S99", direction="aligned", reason="r", delivery_mode="Online")
    with pytest.raises(ToolError):
        tools.search_comments(signal_id="S1", direction="sideways", reason="r", delivery_mode="Online")
    with pytest.raises(ToolError):
        tools.search_comments(signal_id="S1", direction="aligned", reason="", delivery_mode="Online")


def test_search_comments_rejects_unfiltered_call_pointing_to_profile_tool():
    signals, comments = _signals_and_comments()
    tools = InvestigationTools(signals=signals, eligible_comments=comments, ask_analyst_fn=lambda q: "n/a")
    with pytest.raises(ToolError) as excinfo:
        tools.search_comments(signal_id="S1", direction="aligned", reason="looking for support complaints")
    assert "get_comment_corpus_profile" in str(excinfo.value)


def test_search_comments_returns_at_most_five_results():
    signals, comments = _signals_and_comments()
    tools = InvestigationTools(signals=signals, eligible_comments=comments, ask_analyst_fn=lambda q: "n/a")
    online = tools.search_comments(signal_id="S1", direction="aligned", reason="r", delivery_mode="Online")
    assert len(online) <= MAX_SEARCH_RESULTS


def test_search_comments_never_returns_quarantined_comments():
    signals, comments = _signals_and_comments()
    tools = InvestigationTools(signals=signals, eligible_comments=comments, ask_analyst_fn=lambda q: "n/a")
    results = tools.search_comments(signal_id="S1", direction="aligned", reason="r", delivery_mode="Online")
    ids = {c["id"] for c in results}
    assert "FB016" not in ids
    assert "FB018" not in ids


def test_search_comments_filters_by_mode_region_keyword():
    signals, comments = _signals_and_comments()
    tools = InvestigationTools(signals=signals, eligible_comments=comments, ask_analyst_fn=lambda q: "n/a")
    online = tools.search_comments(signal_id="S1", direction="aligned", reason="r", delivery_mode="Online")
    assert online and all(c["mode"] == "Online" for c in online)
    region_a = tools.search_comments(signal_id="S1", direction="aligned", reason="r2", delivery_region="Region A")
    assert region_a and all(c["region"] == "Region A" for c in region_a)
    keyword_hits = tools.search_comments(signal_id="S1", direction="countervailing", reason="r3", keywords=["assessment"])
    assert keyword_hits and all("assessment" in c["text"].lower() for c in keyword_hits)


def test_search_comments_matches_any_of_several_keywords():
    signals, comments = _signals_and_comments()
    tools = InvestigationTools(signals=signals, eligible_comments=comments, ask_analyst_fn=lambda q: "n/a")
    # "replied" and "assessment" match different comments; a multi-keyword
    # search should return the union, not just one or the other alone.
    replied_only = {
        c["id"] for c in tools.search_comments(signal_id="S1", direction="aligned", reason="r", keywords=["replied"])
    }
    assessment_only = {
        c["id"] for c in tools.search_comments(signal_id="S1", direction="countervailing", reason="r", keywords=["assessment"])
    }
    both = {
        c["id"]
        for c in tools.search_comments(signal_id="S2", direction="aligned", reason="r", keywords=["replied", "assessment"])
    }
    assert replied_only and assessment_only  # sanity: both keywords individually match something
    # The combined search matches comments found by either keyword alone
    # (proving OR-matching, not just one keyword winning) — allowing for the
    # 5-result cap possibly truncating the full union.
    assert both.issubset(replied_only | assessment_only)
    assert both & replied_only
    assert both & assessment_only


def test_search_comments_keywords_are_case_insensitive():
    signals, comments = _signals_and_comments()
    tools = InvestigationTools(signals=signals, eligible_comments=comments, ask_analyst_fn=lambda q: "n/a")
    lower = {c["id"] for c in tools.search_comments(signal_id="S1", direction="aligned", reason="r", keywords=["assessment"])}
    upper = {
        c["id"] for c in tools.search_comments(signal_id="S1", direction="countervailing", reason="r", keywords=["ASSESSMENT"])
    }
    assert lower == upper


def test_search_comments_blank_and_duplicate_keyword_entries_are_ignored():
    signals, comments = _signals_and_comments()
    tools = InvestigationTools(signals=signals, eligible_comments=comments, ask_analyst_fn=lambda q: "n/a")
    padded = tools.search_comments(
        signal_id="S1", direction="aligned", reason="r", keywords=["assessment", "", "  ", "assessment"]
    )
    plain = tools.search_comments(signal_id="S2", direction="aligned", reason="r", keywords=["assessment"])
    assert {c["id"] for c in padded} == {c["id"] for c in plain}


def test_search_comments_tolerates_a_bare_string_instead_of_a_list():
    signals, comments = _signals_and_comments()
    tools = InvestigationTools(signals=signals, eligible_comments=comments, ask_analyst_fn=lambda q: "n/a")
    as_string = tools.search_comments(signal_id="S1", direction="aligned", reason="r", keywords="assessment")
    as_list = tools.search_comments(signal_id="S2", direction="aligned", reason="r", keywords=["assessment"])
    assert {c["id"] for c in as_string} == {c["id"] for c in as_list}


def test_search_comments_rejects_call_with_only_blank_keywords_and_no_other_filter():
    signals, comments = _signals_and_comments()
    tools = InvestigationTools(signals=signals, eligible_comments=comments, ask_analyst_fn=lambda q: "n/a")
    with pytest.raises(ToolError):
        tools.search_comments(signal_id="S1", direction="aligned", reason="r", keywords=["", "   "])


def test_search_comments_dedup_is_order_and_case_independent_for_keyword_sets():
    signals, comments = _signals_and_comments()
    tools = InvestigationTools(signals=signals, eligible_comments=comments, ask_analyst_fn=lambda q: "n/a")
    tools.search_comments(signal_id="S1", direction="aligned", reason="r", keywords=["reply", "help"])
    with pytest.raises(ToolError):
        # Same two keywords, different order and case — still a repeat.
        tools.search_comments(signal_id="S1", direction="aligned", reason="different reason", keywords=["HELP", "Reply"])
    # A genuinely different keyword set is not a repeat.
    tools.search_comments(signal_id="S1", direction="aligned", reason="r2", keywords=["reply"])
    assert tools.search_count("S1", "aligned") == 2


def test_search_comments_enforces_per_direction_budget_and_records_limitation():
    signals, comments = _signals_and_comments()
    tools = InvestigationTools(signals=signals, eligible_comments=comments, ask_analyst_fn=lambda q: "n/a")
    for i, kw in enumerate(["assessment", "help", "reply"]):
        tools.search_comments(signal_id="S1", direction="aligned", reason=f"attempt {i}", keywords=[kw])
    assert tools.search_count("S1", "aligned") == MAX_SEARCHES_PER_DIRECTION
    assert "S1" not in tools.limitations  # not exhausted yet, just at the cap

    with pytest.raises(ToolError):
        tools.search_comments(signal_id="S1", direction="aligned", reason="one more", keywords=["teacher"])

    assert any("budget" in text.lower() for text in tools.limitations["S1"])
    # The other direction is untouched by the aligned-direction budget.
    assert tools.search_count("S1", "countervailing") == 0


def test_search_comments_enforces_global_budget_and_records_limitation():
    signals, comments = _signals_and_comments()
    tools = InvestigationTools(signals=signals, eligible_comments=comments, ask_analyst_fn=lambda q: "n/a")
    tools.total_searches = MAX_SEARCHES_PER_RUN  # simulate an exhausted run-wide budget

    with pytest.raises(ToolError):
        tools.search_comments(signal_id="S2", direction="aligned", reason="r", delivery_mode="Online")

    assert any("run-wide" in text.lower() or "budget" in text.lower() for text in tools.limitations["S2"])


def test_search_comments_rejects_identical_repeat():
    signals, comments = _signals_and_comments()
    tools = InvestigationTools(signals=signals, eligible_comments=comments, ask_analyst_fn=lambda q: "n/a")
    tools.search_comments(signal_id="S1", direction="aligned", reason="r", delivery_mode="Online")
    with pytest.raises(ToolError):
        tools.search_comments(signal_id="S1", direction="aligned", reason="different reason, same filters", delivery_mode="Online")


def test_search_comments_allows_same_filters_across_different_directions():
    signals, comments = _signals_and_comments()
    tools = InvestigationTools(signals=signals, eligible_comments=comments, ask_analyst_fn=lambda q: "n/a")
    tools.search_comments(signal_id="S1", direction="aligned", reason="r", delivery_mode="Online")
    # Same signal, same filter, different direction — not a duplicate.
    tools.search_comments(signal_id="S1", direction="countervailing", reason="r2", delivery_mode="Online")
    assert tools.search_count("S1", "aligned") == 1
    assert tools.search_count("S1", "countervailing") == 1


def test_record_link_rejects_unknown_signal_id():
    signals, comments = _signals_and_comments()
    tools = InvestigationTools(signals=signals, eligible_comments=comments, ask_analyst_fn=lambda q: "n/a")
    with pytest.raises(ToolError):
        tools.record_link("S99", "FB003", "aligned", "Digital platform", "note")


def test_record_link_rejects_unknown_or_quarantined_comment_id():
    signals, comments = _signals_and_comments()
    tools = InvestigationTools(signals=signals, eligible_comments=comments, ask_analyst_fn=lambda q: "n/a")
    with pytest.raises(ToolError):
        tools.record_link("S1", "FB016", "aligned", "Teacher responsiveness and support", "note")  # quarantined
    with pytest.raises(ToolError):
        tools.record_link("S1", "FB999", "aligned", "Teacher responsiveness and support", "note")  # unknown


def test_record_link_rejects_invalid_relation():
    signals, comments = _signals_and_comments()
    tools = InvestigationTools(signals=signals, eligible_comments=comments, ask_analyst_fn=lambda q: "n/a")
    with pytest.raises(ToolError):
        tools.record_link("S1", "FB003", "neutral", "Digital platform", "note")


def test_record_link_valid_updates_state_and_moving_relation_removes_prior():
    signals, comments = _signals_and_comments()
    tools = InvestigationTools(signals=signals, eligible_comments=comments, ask_analyst_fn=lambda q: "n/a")
    tools.record_link("S1", "FB003", "aligned", "Teacher responsiveness and support", "reports slow replies")
    assert tools.aligned_ids("S1") == ["FB003"]
    tools.record_link("S1", "FB003", "countervailing", "Teacher responsiveness and support", "reclassified")
    assert tools.aligned_ids("S1") == []
    assert tools.countervailing_ids("S1") == ["FB003"]


def test_note_no_evidence_and_ask_analyst():
    signals, comments = _signals_and_comments()
    asked = []
    tools = InvestigationTools(
        signals=signals, eligible_comments=comments, ask_analyst_fn=lambda q: asked.append(q) or "use judgement"
    )
    tools.note_no_evidence("S3", "No eligible comments about this unit.")
    assert tools.no_evidence["S3"] == "No eligible comments about this unit."
    answer = tools.ask_analyst("Is FB024 about withdrawal or just support?")
    assert answer == {"answer": "use judgement"}
    assert asked == ["Is FB024 about withdrawal or just support?"]


# --- finish_investigation gating and limitations ---------------------------


def _search_both_directions(tools, signal_id, keyword_prefix="k"):
    tools.search_comments(signal_id=signal_id, direction="aligned", reason="r", keywords=[f"{keyword_prefix}a"])
    tools.search_comments(signal_id=signal_id, direction="countervailing", reason="r", keywords=[f"{keyword_prefix}b"])


def test_unmet_requirements_lists_signals_missing_direction_searches():
    signals, comments = _signals_and_comments()
    tools = InvestigationTools(signals=signals, eligible_comments=comments, ask_analyst_fn=lambda q: "n/a")
    unmet = tools.unmet_requirements()
    assert len(unmet) == len(signals)  # nothing searched yet
    assert all("S" in u for u in unmet)


def test_unmet_requirements_empty_once_both_directions_searched_for_every_signal():
    signals, comments = _signals_and_comments()
    tools = InvestigationTools(signals=signals, eligible_comments=comments, ask_analyst_fn=lambda q: "n/a")
    for i, signal in enumerate(signals):
        _search_both_directions(tools, signal.id, keyword_prefix=f"zz{i}")
    assert tools.unmet_requirements() == []


def test_unmet_requirements_empty_when_run_wide_budget_exhausted():
    signals, comments = _signals_and_comments()
    tools = InvestigationTools(signals=signals, eligible_comments=comments, ask_analyst_fn=lambda q: "n/a")
    tools.total_searches = MAX_SEARCHES_PER_RUN
    # Nothing was actually searched, but nothing more CAN be done — excused.
    assert tools.unmet_requirements() == []


def test_finish_investigation_returns_not_finished_with_unmet_requirements():
    signals, comments = _signals_and_comments()
    tools = InvestigationTools(signals=signals, eligible_comments=comments, ask_analyst_fn=lambda q: "n/a")
    result = tools.finish_investigation()
    assert result["status"] == "not_finished"
    assert len(result["unmet_requirements"]) == len(signals)
    assert tools.finished is False


def test_finish_investigation_succeeds_once_requirements_met():
    signals, comments = _signals_and_comments()
    tools = InvestigationTools(signals=signals, eligible_comments=comments, ask_analyst_fn=lambda q: "n/a")
    for i, signal in enumerate(signals):
        _search_both_directions(tools, signal.id, keyword_prefix=f"zz{i}")
    result = tools.finish_investigation()
    assert result == {"status": "finished"}
    assert tools.finished is True


def test_finalize_limitations_records_gap_for_unsearched_direction():
    tools = InvestigationTools(signals=[_one_signal()], eligible_comments=_two_comments(), ask_analyst_fn=lambda q: "n/a")
    tools.search_comments(signal_id="S1", direction="aligned", reason="r", keywords=["waited"])
    # countervailing was never searched
    tools.finalize_limitations()
    assert any("countervailing" in text for text in tools.limitations["S1"])
    assert "aligned" not in " ".join(tools.limitations.get("S1", []))  # aligned WAS searched, no gap there


def test_finalize_limitations_does_not_duplicate_text():
    tools = InvestigationTools(signals=[_one_signal()], eligible_comments=_two_comments(), ask_analyst_fn=lambda q: "n/a")
    tools.finalize_limitations()
    tools.finalize_limitations()
    # Calling it twice must not double up the same limitation text.
    assert len(tools.limitations["S1"]) == len(set(tools.limitations["S1"]))


def test_build_plan_structure_and_no_comment_text_leak():
    tools = InvestigationTools(signals=[_one_signal()], eligible_comments=_two_comments(), ask_analyst_fn=lambda q: "n/a")
    tools.search_comments(signal_id="S1", direction="aligned", reason="looking for support complaints", keywords=["waited"])
    tools.record_link("S1", "FB003", "aligned", "Teacher responsiveness and support", "reports a delayed reply")
    tools.finalize_limitations()
    plan = tools.build_plan()

    assert plan["total_searches"] == 1
    assert plan["signals"][0]["signal_id"] == "S1"
    assert plan["signals"][0]["searches"] == {"aligned": 1, "countervailing": 0}
    assert plan["signals"][0]["aligned_ids"] == ["FB003"]
    assert len(plan["search_log"]) == 1
    assert plan["search_log"][0]["result_ids"] == ["FB003"]
    assert plan["search_log"][0]["reason"] == "looking for support complaints"

    blob = json.dumps(plan)
    for comment in _two_comments():
        assert comment.text not in blob


# --- run_investigation orchestration (mocked client) ----------------------


class _FakeFunctionCall:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.function = _FakeFunctionCall(name, arguments)


class _FakeMessage:
    def __init__(self, tool_calls=None, content=None):
        self.tool_calls = tool_calls
        self.content = content
        self.role = "assistant"

    def model_dump(self, exclude_none=True):
        d = {"role": self.role}
        if self.content is not None:
            d["content"] = self.content
        if self.tool_calls:
            d["tool_calls"] = [
                {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in self.tool_calls
            ]
        return d


class _FakeChoice:
    def __init__(self, message):
        self.message = message


class _FakeResponse:
    def __init__(self, message):
        self.choices = [_FakeChoice(message)]


class _FakeClient:
    def __init__(self, messages):
        self._messages = list(messages)
        self.create_calls = 0
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.create_calls += 1
        message = self._messages.pop(0)
        return _FakeResponse(message)


def _call(call_id, name, args):
    return _FakeToolCall(call_id, name, json.dumps(args))


def test_run_investigation_with_no_signals_skips_the_api():
    tools = run_investigation(
        signals=[], eligible_comments=[], ask_analyst_fn=lambda q: "n/a", client="should-not-be-used"
    )
    assert tools.finished is False


def test_run_investigation_with_no_eligible_comments_auto_notes_every_signal():
    signals, _ = _signals_and_comments()
    plan_events = []
    tools = run_investigation(
        signals=signals,
        eligible_comments=[],
        ask_analyst_fn=lambda q: "n/a",
        audit_log_event_fn=lambda event, payload: plan_events.append((event, payload)),
        client="should-not-be-used",
    )
    assert set(tools.no_evidence) == {s.id for s in signals}
    # Even the auto-notified path records why each direction was never searched.
    for signal in signals:
        assert any("aligned" in t for t in tools.limitations[signal.id])
        assert any("countervailing" in t for t in tools.limitations[signal.id])
    assert plan_events and plan_events[0][0] == "investigation_plan"


def test_run_investigation_drives_tool_loop_to_completion():
    signal = _one_signal()
    comments = _two_comments()
    fake_client = _FakeClient(
        [
            _FakeMessage(tool_calls=[_call("c1", "get_comment_corpus_profile", {})]),
            _FakeMessage(
                tool_calls=[_call("c2", "search_comments", {"signal_id": "S1", "direction": "aligned", "reason": "looking for complaints", "keywords": ["waited"]})]
            ),
            _FakeMessage(
                tool_calls=[_call("c3", "search_comments", {"signal_id": "S1", "direction": "countervailing", "reason": "looking for praise", "keywords": ["replied"]})]
            ),
            _FakeMessage(
                tool_calls=[
                    _call(
                        "c4",
                        "record_link",
                        {
                            "signal_id": "S1",
                            "comment_id": "FB003",
                            "relation": "aligned",
                            "theme": "Teacher responsiveness and support",
                            "rationale": "reports a delayed reply",
                        },
                    )
                ]
            ),
            _FakeMessage(tool_calls=[_call("c5", "finish_investigation", {})]),
        ]
    )
    logged_calls = []
    plan_events = []
    tools = run_investigation(
        signals=[signal],
        eligible_comments=comments,
        ask_analyst_fn=lambda q: "n/a",
        audit_log_tool_call_fn=lambda name, args, result: logged_calls.append((name, args, result)),
        audit_log_event_fn=lambda event, payload: plan_events.append((event, payload)),
        client=fake_client,
        model="fake-model",
    )
    assert tools.finished is True
    assert tools.aligned_ids("S1") == ["FB003"]
    assert fake_client.create_calls == 5
    logged_names = [name for name, _, _ in logged_calls]
    assert logged_names == ["get_comment_corpus_profile", "search_comments", "search_comments", "record_link", "finish_investigation"]

    # Tool-call args must never carry raw comment text.
    for _, args, _ in logged_calls:
        assert "text" not in args

    # search_comments results are logged as IDs and a count only, never text.
    search_results = [result for name, _, result in logged_calls if name == "search_comments"]
    assert search_results[0] == {"result_ids": ["FB003"], "count": 1}
    assert search_results[1] == {"result_ids": ["FB005"], "count": 1}
    for _, _, result in logged_calls:
        blob = json.dumps(result)
        for comment in comments:
            assert comment.text not in blob

    assert plan_events and plan_events[0][0] == "investigation_plan"
    assert plan_events[0][1]["finished"] is True


def test_run_investigation_finish_rejected_then_accepted_after_more_searches():
    signal = _one_signal()
    comments = _two_comments()
    fake_client = _FakeClient(
        [
            # First attempt: nothing searched yet — must be rejected.
            _FakeMessage(tool_calls=[_call("c1", "finish_investigation", {})]),
            _FakeMessage(
                tool_calls=[_call("c2", "search_comments", {"signal_id": "S1", "direction": "aligned", "reason": "r", "keywords": ["waited"]})]
            ),
            _FakeMessage(
                tool_calls=[_call("c3", "search_comments", {"signal_id": "S1", "direction": "countervailing", "reason": "r", "keywords": ["replied"]})]
            ),
            _FakeMessage(tool_calls=[_call("c4", "note_no_evidence", {"signal_id": "S1", "reason": "nothing clearly on point"})]),
            # Second attempt: now it should succeed.
            _FakeMessage(tool_calls=[_call("c5", "finish_investigation", {})]),
        ]
    )
    logged_calls = []
    tools = run_investigation(
        signals=[signal],
        eligible_comments=comments,
        ask_analyst_fn=lambda q: "n/a",
        audit_log_tool_call_fn=lambda name, args, result: logged_calls.append((name, args, result)),
        client=fake_client,
        model="fake-model",
    )
    assert tools.finished is True
    finish_results = [result for name, _, result in logged_calls if name == "finish_investigation"]
    assert finish_results[0]["status"] == "not_finished"
    assert finish_results[0]["unmet_requirements"]
    assert finish_results[1] == {"status": "finished"}


def test_run_investigation_reports_tool_error_without_raising():
    signals, comments = _signals_and_comments()
    fake_client = _FakeClient(
        [
            _FakeMessage(
                tool_calls=[
                    _call(
                        "c1",
                        "record_link",
                        {
                            "signal_id": "S1",
                            "comment_id": "FB016",  # quarantined
                            "relation": "aligned",
                            "theme": "Teacher responsiveness and support",
                            "rationale": "x",
                        },
                    )
                ]
            ),
            _FakeMessage(tool_calls=[_call("c2", "finish_investigation", {})]),
        ]
    )
    tools = run_investigation(
        signals=signals,
        eligible_comments=comments,
        ask_analyst_fn=lambda q: "n/a",
        client=fake_client,
        model="fake-model",
        max_tool_calls=2,
    )
    assert tools.aligned_ids("S1") == []
    assert fake_client.create_calls == 2


def test_run_investigation_rejects_unknown_tool_name_without_raising():
    signals, comments = _signals_and_comments()
    fake_client = _FakeClient(
        [
            _FakeMessage(tool_calls=[_call("c1", "delete_all_data", {})]),
            _FakeMessage(tool_calls=[_call("c2", "finish_investigation", {})]),
        ]
    )
    tools = run_investigation(
        signals=signals,
        eligible_comments=comments,
        ask_analyst_fn=lambda q: "n/a",
        client=fake_client,
        model="fake-model",
        max_tool_calls=2,
    )
    assert fake_client.create_calls == 2  # loop survives the malformed/unknown call


def test_run_investigation_stops_at_max_tool_calls():
    signals, comments = _signals_and_comments()
    fake_client = _FakeClient(
        [
            _FakeMessage(tool_calls=[_call("c1", "list_signals", {})]),
            _FakeMessage(tool_calls=[_call("c2", "search_comments", {})]),
            _FakeMessage(tool_calls=[_call("c3", "record_link", {})]),  # would raise if ever reached
        ]
    )
    tools = run_investigation(
        signals=signals,
        eligible_comments=comments,
        ask_analyst_fn=lambda q: "n/a",
        client=fake_client,
        model="fake-model",
        max_tool_calls=2,
    )
    assert fake_client.create_calls == 2
    assert tools.finished is False
