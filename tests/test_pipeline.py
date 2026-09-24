"""End-to-end run of the whole pipeline via examples/demo_answers.json,
with the OpenAI client replaced by a scripted fake (no network/API key)."""
import json

from cpc_agent import config
from cpc_agent.main import run_pipeline

FORBIDDEN_STRINGS = [
    "Daniel Hughes",
    "Hughes",
    "Ignore all previous instructions",
    "poor performing",
    "caused by",
]

DEMO_ANSWERS = config.BASE_DIR / "examples" / "demo_answers.json"


class _FakeFunctionCall:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.function = _FakeFunctionCall(name, arguments)


class _FakeMessage:
    def __init__(self, tool_calls):
        self.tool_calls = tool_calls
        self.content = None
        self.role = "assistant"

    def model_dump(self, exclude_none=True):
        return {
            "role": self.role,
            "tool_calls": [
                {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in self.tool_calls
            ],
        }


class _FakeChoice:
    def __init__(self, message):
        self.message = message


class _FakeResponse:
    def __init__(self, message):
        self.choices = [_FakeChoice(message)]


_DEFAULT_SCRIPT = [
    ("get_comment_corpus_profile", {}),
    ("list_signals", {}),
    ("search_comments", {"signal_id": "S1", "direction": "aligned", "reason": "looking for support complaints", "keyword": "reply"}),
    ("search_comments", {"signal_id": "S1", "direction": "countervailing", "reason": "looking for support praise", "keyword": "detailed"}),
    *[
        ("record_link", {"signal_id": "S1", "comment_id": cid, "relation": "aligned", "theme": "Teacher responsiveness and support", "rationale": "reports a support gap"})
        for cid in ("FB003", "FB007", "FB011", "FB015")
    ],
    *[
        ("record_link", {"signal_id": "S1", "comment_id": cid, "relation": "countervailing", "theme": "Teacher responsiveness and support", "rationale": "reports responsive teaching"})
        for cid in ("FB005", "FB019")
    ],
    ("search_comments", {"signal_id": "S2", "direction": "aligned", "reason": "looking for clarity complaints", "keyword": "assessment"}),
    ("search_comments", {"signal_id": "S2", "direction": "countervailing", "reason": "looking for clarity praise", "keyword": "checklist"}),
    *[
        ("record_link", {"signal_id": "S2", "comment_id": cid, "relation": "aligned", "theme": "Assessment clarity", "rationale": "reports unclear assessment wording"})
        for cid in ("FB002", "FB009", "FB013")
    ],
    ("record_link", {"signal_id": "S2", "comment_id": "FB017", "relation": "countervailing", "theme": "Assessment clarity", "rationale": "reports clear checklists"}),
    ("search_comments", {"signal_id": "S3", "direction": "aligned", "reason": "looking for unit-specific complaints", "keyword": "legal"}),
    ("search_comments", {"signal_id": "S3", "direction": "countervailing", "reason": "looking for unit-specific praise", "keyword": "ethical"}),
    ("note_no_evidence", {"signal_id": "S3", "reason": "No eligible comments could be linked to this unit."}),
    ("search_comments", {"signal_id": "S4", "direction": "aligned", "reason": "looking for withdrawal-related comments", "keyword": "withdraw"}),
    ("search_comments", {"signal_id": "S4", "direction": "countervailing", "reason": "looking for continuation comments", "keyword": "flexib"}),
    ("record_link", {"signal_id": "S4", "comment_id": "FB024", "relation": "aligned", "theme": "Flexibility and course pacing", "rationale": "reports nearly withdrawing"}),
    ("finish_investigation", {}),
]


class _FakeClient:
    """Plays back a fixed script of tool calls that reproduces
    tests/fixtures/expected_results.json's aligned/countervailing links
    (or a caller-supplied script for a different scenario)."""

    def __init__(self, script=None):
        script = script if script is not None else _DEFAULT_SCRIPT
        self._messages = [
            _FakeMessage([_FakeToolCall(f"c{i}", name, json.dumps(args))]) for i, (name, args) in enumerate(script)
        ]
        self.create_calls = 0
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.create_calls += 1
        return _FakeResponse(self._messages.pop(0))


def test_end_to_end_run_produces_all_four_outputs(tmp_path):
    result = run_pipeline(
        answers_path=str(DEMO_ANSWERS),
        data_dir=config.DATA_DIR,
        runs_dir=tmp_path,
        client=_FakeClient(),
        model="fake-model",
    )
    assert result == 0

    run_dirs = list(tmp_path.iterdir())
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    for filename in ("evidence_records.json", "exception_log.json", "audit_log.jsonl", "brief.md", "brief.html"):
        assert (run_dir / filename).exists(), filename

    evidence_records = json.loads((run_dir / "evidence_records.json").read_text(encoding="utf-8"))
    by_id = {r["finding_id"]: r for r in evidence_records}
    assert by_id["S1"]["evidence_status"] == "Mixed"
    assert by_id["S2"]["evidence_status"] == "Mixed"
    assert by_id["S3"]["evidence_status"] == "No qualitative evidence"
    assert by_id["S4"]["evidence_status"] == "Limited"
    assert all(r["reviewer_status"] == "accept" for r in evidence_records)
    assert all(r["reviewer"] == "Demo Analyst" for r in evidence_records)
    # JSON records always keep the raw code names, never the readable brief labels.
    assert by_id["S1"]["measure"] == "teacher_support"
    # The analyst made no excerpt choice in examples/demo_answers.json -> automatic default,
    # recorded per side, alongside the full linked set (not just the excerpt subset).
    assert by_id["S1"]["aligned_excerpt_ids"] == by_id["S1"]["aligned_ids"][:2]
    assert by_id["S1"]["aligned_excerpt_selection"] == "automatic selection (first two by ID)"
    assert by_id["S1"]["aligned_ids"] == ["FB003", "FB007", "FB011", "FB015"]  # full linked set retained

    exceptions = json.loads((run_dir / "exception_log.json").read_text(encoding="utf-8"))
    quarantined_ids = {q["comment_id"] for q in exceptions["quarantined"]}
    assert quarantined_ids == {"FB016", "FB018"}
    for q in exceptions["quarantined"]:
        assert "Hughes" not in json.dumps(q)
        assert "Ignore all previous instructions" not in json.dumps(q)
        assert q["summary"]  # plain-language summary always present
    assert any("Region C" in e["detail"] for e in exceptions["exclusions"])
    region_c_exclusion = next(e for e in exceptions["exclusions"] if "Region C" in e["detail"])
    assert region_c_exclusion["summary"] == "Region C blended delivery was excluded because the 2025 withdrawal figure was missing."

    audit_lines = (run_dir / "audit_log.jsonl").read_text(encoding="utf-8").strip().splitlines()
    events = [json.loads(line) for line in audit_lines]
    assert events[0]["event"] == "run_start"
    assert events[-1]["event"] == "run_end"
    assert events[-1]["status"] == "completed"
    assert events[-1]["final_decision"] == "approve"

    brief_text = (run_dir / "brief.md").read_text(encoding="utf-8")
    for term in FORBIDDEN_STRINGS:
        assert term not in brief_text
    assert "Status: Approved" in brief_text
    assert "S1" in brief_text and "S4" in brief_text
    assert "Analyst: Demo Analyst" in brief_text

    # Final decision was "approve" (see examples/demo_answers.json): the
    # DRAFT marker and "(not approved)" qualifier must be gone.
    assert "DRAFT" not in brief_text
    assert "(not approved)" not in brief_text
    assert "# Course Performance Workshop Brief" in brief_text

    # No leftover bracket placeholder lines from the template.
    assert "[Files used, periods, any sources unavailable]" not in brief_text
    assert "[Only findings the analyst has approved" not in brief_text
    assert "[Missing data, quarantined comments, low samples, unresolved issues]" not in brief_text
    assert "[Neutral questions linked to signal IDs. No recommendations.]" not in brief_text

    # Readable measure labels, not raw code names, in the brief.
    assert "Teacher support satisfaction (Online)" in brief_text
    assert "teacher_support (Online)" not in brief_text

    # "What's not included" is plain language; technical field syntax stays
    # in exception_log.json only.
    assert "Region C blended delivery was excluded because the 2025 withdrawal figure was missing." in brief_text
    assert "delivery_region=Region C" not in brief_text
    assert "reporting_period=2025" not in brief_text

    # brief.html: same guarantees, plus HTML-specific ones (excerpts, gauges).
    html_text = (run_dir / "brief.html").read_text(encoding="utf-8")
    for term in FORBIDDEN_STRINGS:
        assert term not in html_text
    assert "S9" not in html_text  # no rejected finding would ever be S9 here, but guards the pattern
    assert all(r["finding_id"] in html_text for r in evidence_records if r["reviewer_status"] == "accept")
    assert "Hughes" not in html_text
    assert "Ignore all previous instructions" not in html_text
    assert "Demo Analyst" in html_text
    assert html_text.count("<blockquote") <= 2 * len(evidence_records)  # at most 2 excerpts per side per signal
    assert 'style="width:61%"' in html_text  # S1's gauge fill, computed from real data
    assert "Teacher support satisfaction" in html_text
    assert "teacher_support" not in html_text

    # Plain-language exceptions in the HTML brief too, not the technical detail.
    assert "Region C blended delivery was excluded because the 2025 withdrawal figure was missing." in html_text
    assert "delivery_region=Region C" not in html_text

    # No excerpt choice was made in examples/demo_answers.json -> automatic default, labelled as such.
    assert "Excerpts: automatic selection (first two by ID)." in html_text

    # brief.html works offline: no external resources at all.
    assert "googleapis" not in html_text
    assert "<link" not in html_text
    assert "https://" not in html_text
    assert "Public Sans" not in html_text
    assert "Source Serif 4" not in html_text


def test_end_to_end_run_never_estimates_region_c_withdrawals(tmp_path):
    result = run_pipeline(
        answers_path=str(DEMO_ANSWERS),
        data_dir=config.DATA_DIR,
        runs_dir=tmp_path,
        client=_FakeClient(),
        model="fake-model",
    )
    assert result == 0
    run_dir = next(tmp_path.iterdir())

    evidence_records = json.loads((run_dir / "evidence_records.json").read_text(encoding="utf-8"))
    assert not any(r["segment"] == "Region C" for r in evidence_records)

    exceptions = json.loads((run_dir / "exception_log.json").read_text(encoding="utf-8"))
    assert any("Region C" in e["detail"] and "course_metrics.csv" in e["detail"] for e in exceptions["exclusions"])


def test_end_to_end_run_records_search_budget_exhaustion_as_a_limitation(tmp_path):
    """S1's aligned direction is searched 4 times (one over the 3-search
    budget); the 4th is rejected inside the tool loop, which must surface
    as a recorded limitation — on S1's evidence record and in
    exception_log.json — never as a silently weaker link."""
    script = [
        ("get_comment_corpus_profile", {}),
        ("list_signals", {}),
        ("search_comments", {"signal_id": "S1", "direction": "aligned", "reason": "r1", "keyword": "reply"}),
        ("search_comments", {"signal_id": "S1", "direction": "aligned", "reason": "r2", "keyword": "help"}),
        ("search_comments", {"signal_id": "S1", "direction": "aligned", "reason": "r3", "keyword": "wait"}),
        ("search_comments", {"signal_id": "S1", "direction": "aligned", "reason": "r4 over budget", "keyword": "teacher"}),
        ("search_comments", {"signal_id": "S1", "direction": "countervailing", "reason": "r5", "keyword": "detailed"}),
        ("record_link", {"signal_id": "S1", "comment_id": "FB003", "relation": "aligned", "theme": "Teacher responsiveness and support", "rationale": "reports a support gap"}),
        ("search_comments", {"signal_id": "S2", "direction": "aligned", "reason": "r", "keyword": "assessment"}),
        ("search_comments", {"signal_id": "S2", "direction": "countervailing", "reason": "r", "keyword": "checklist"}),
        ("note_no_evidence", {"signal_id": "S2", "reason": "nothing conclusive"}),
        ("search_comments", {"signal_id": "S3", "direction": "aligned", "reason": "r", "keyword": "legal"}),
        ("search_comments", {"signal_id": "S3", "direction": "countervailing", "reason": "r", "keyword": "ethical"}),
        ("note_no_evidence", {"signal_id": "S3", "reason": "nothing conclusive"}),
        ("search_comments", {"signal_id": "S4", "direction": "aligned", "reason": "r", "keyword": "withdraw"}),
        ("search_comments", {"signal_id": "S4", "direction": "countervailing", "reason": "r", "keyword": "flexib"}),
        ("note_no_evidence", {"signal_id": "S4", "reason": "nothing conclusive"}),
        ("finish_investigation", {}),
    ]
    result = run_pipeline(
        answers_path=str(DEMO_ANSWERS),
        data_dir=config.DATA_DIR,
        runs_dir=tmp_path,
        client=_FakeClient(script),
        model="fake-model",
    )
    assert result == 0
    run_dir = next(tmp_path.iterdir())

    evidence_records = json.loads((run_dir / "evidence_records.json").read_text(encoding="utf-8"))
    by_id = {r["finding_id"]: r for r in evidence_records}
    # The rejected 4th search never weakens the link that WAS found — FB003 is still aligned.
    assert by_id["S1"]["aligned_ids"] == ["FB003"]
    assert "budget" in by_id["S1"]["limitation"].lower()

    exceptions = json.loads((run_dir / "exception_log.json").read_text(encoding="utf-8"))
    limitation_summaries = " ".join(e["summary"] for e in exceptions["limitations"])
    assert "S1" in limitation_summaries
    assert "budget" in limitation_summaries.lower()

    audit_lines = (run_dir / "audit_log.jsonl").read_text(encoding="utf-8").strip().splitlines()
    events = [json.loads(line) for line in audit_lines]
    limitation_events = [e for e in events if e.get("event") == "investigation_limitation"]
    assert any(e["signal_id"] == "S1" and "budget" in e["summary"].lower() for e in limitation_events)

    plan_events = [e for e in events if e.get("event") == "investigation_plan"]
    assert len(plan_events) == 1
    plan = plan_events[0]
    assert plan["total_searches"] >= 3  # the rejected 4th attempt is not counted as a successful search
    s1_plan = next(s for s in plan["signals"] if s["signal_id"] == "S1")
    assert s1_plan["searches"]["aligned"] == 3  # capped at the budget, not 4
