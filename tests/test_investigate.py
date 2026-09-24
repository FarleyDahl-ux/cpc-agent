import json

import pytest

from cpc_agent import config
from cpc_agent.investigate import (
    InvestigationTools,
    ToolError,
    run_investigation,
)
from cpc_agent.loaders import load_approved_sources
from cpc_agent.metrics import calculate_metrics, flag_signals
from cpc_agent.screening import screen_comments

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


# --- InvestigationTools (deterministic, no API) --------------------------


def test_list_signals_reports_all_flagged_signals():
    signals, comments = _signals_and_comments()
    tools = InvestigationTools(signals=signals, eligible_comments=comments, ask_analyst_fn=lambda q: "n/a")
    listed = tools.list_signals()
    assert {s["id"] for s in listed} == {"S1", "S2", "S3", "S4"}


def test_search_comments_filters_by_mode_region_keyword():
    signals, comments = _signals_and_comments()
    tools = InvestigationTools(signals=signals, eligible_comments=comments, ask_analyst_fn=lambda q: "n/a")
    online = tools.search_comments(delivery_mode="Online")
    assert online and all(c["mode"] == "Online" for c in online)
    region_a = tools.search_comments(delivery_region="Region A")
    assert region_a and all(c["region"] == "Region A" for c in region_a)
    keyword_hits = tools.search_comments(keyword="assessment")
    assert keyword_hits and all("assessment" in c["text"].lower() for c in keyword_hits)


def test_search_comments_never_returns_quarantined_comments():
    signals, comments = _signals_and_comments()
    tools = InvestigationTools(signals=signals, eligible_comments=comments, ask_analyst_fn=lambda q: "n/a")
    all_results = tools.search_comments()
    ids = {c["id"] for c in all_results}
    assert "FB016" not in ids
    assert "FB018" not in ids


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


def test_run_investigation_with_no_signals_skips_the_api():
    tools = run_investigation(
        signals=[], eligible_comments=[], ask_analyst_fn=lambda q: "n/a", client="should-not-be-used"
    )
    assert tools.finished is False


def test_run_investigation_with_no_eligible_comments_auto_notes_every_signal():
    signals, _ = _signals_and_comments()
    tools = run_investigation(
        signals=signals, eligible_comments=[], ask_analyst_fn=lambda q: "n/a", client="should-not-be-used"
    )
    assert set(tools.no_evidence) == {s.id for s in signals}


def test_run_investigation_drives_tool_loop_to_completion():
    signals, comments = _signals_and_comments()
    fake_client = _FakeClient(
        [
            _FakeMessage(tool_calls=[_FakeToolCall("c1", "list_signals", "{}")]),
            _FakeMessage(
                tool_calls=[_FakeToolCall("c2", "search_comments", json.dumps({"delivery_mode": "Online"}))]
            ),
            _FakeMessage(
                tool_calls=[
                    _FakeToolCall(
                        "c3",
                        "record_link",
                        json.dumps(
                            {
                                "signal_id": "S1",
                                "comment_id": "FB003",
                                "relation": "aligned",
                                "theme": "Teacher responsiveness and support",
                                "rationale": "reports a delayed reply",
                            }
                        ),
                    )
                ]
            ),
            _FakeMessage(tool_calls=[_FakeToolCall("c4", "finish_investigation", "{}")]),
        ]
    )
    logged_calls = []
    tools = run_investigation(
        signals=signals,
        eligible_comments=comments,
        ask_analyst_fn=lambda q: "n/a",
        audit_log_tool_call_fn=lambda name, args: logged_calls.append((name, args)),
        client=fake_client,
        model="fake-model",
    )
    assert tools.finished is True
    assert tools.aligned_ids("S1") == ["FB003"]
    assert fake_client.create_calls == 4
    logged_names = [name for name, _ in logged_calls]
    assert logged_names == ["list_signals", "search_comments", "record_link", "finish_investigation"]
    # Tool-call args must never carry raw comment text.
    for _, args in logged_calls:
        assert "text" not in args


def test_run_investigation_reports_tool_error_without_raising():
    signals, comments = _signals_and_comments()
    fake_client = _FakeClient(
        [
            _FakeMessage(
                tool_calls=[
                    _FakeToolCall(
                        "c1",
                        "record_link",
                        json.dumps(
                            {
                                "signal_id": "S1",
                                "comment_id": "FB016",  # quarantined
                                "relation": "aligned",
                                "theme": "Teacher responsiveness and support",
                                "rationale": "x",
                            }
                        ),
                    )
                ]
            ),
            _FakeMessage(tool_calls=[_FakeToolCall("c2", "finish_investigation", "{}")]),
        ]
    )
    tools = run_investigation(
        signals=signals, eligible_comments=comments, ask_analyst_fn=lambda q: "n/a", client=fake_client, model="fake-model"
    )
    assert tools.aligned_ids("S1") == []
    assert tools.finished is True


def test_run_investigation_rejects_unknown_tool_name_without_raising():
    signals, comments = _signals_and_comments()
    fake_client = _FakeClient(
        [
            _FakeMessage(tool_calls=[_FakeToolCall("c1", "delete_all_data", "{}")]),
            _FakeMessage(tool_calls=[_FakeToolCall("c2", "finish_investigation", "{}")]),
        ]
    )
    tools = run_investigation(
        signals=signals, eligible_comments=comments, ask_analyst_fn=lambda q: "n/a", client=fake_client, model="fake-model"
    )
    assert tools.finished is True  # loop survives the malformed/unknown call


def test_run_investigation_stops_at_max_tool_calls():
    signals, comments = _signals_and_comments()
    fake_client = _FakeClient(
        [
            _FakeMessage(tool_calls=[_FakeToolCall("c1", "list_signals", "{}")]),
            _FakeMessage(tool_calls=[_FakeToolCall("c2", "search_comments", "{}")]),
            _FakeMessage(tool_calls=[_FakeToolCall("c3", "record_link", "{}")]),  # would raise if ever reached
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
