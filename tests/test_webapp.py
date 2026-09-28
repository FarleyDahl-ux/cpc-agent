"""Flask test-client tests for the review dashboard, with the OpenAI
client replaced by a scripted fake (no network/API key needed)."""
import json

import pytest

from cpc_agent import config
from cpc_agent.webapp import create_app

FORBIDDEN_STRINGS = [
    "Daniel Hughes",
    "Hughes",
    "Ignore all previous instructions",
    "poor performing",
    "caused by",
]


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


class _FakeClient:
    def __init__(self, script):
        self._messages = [
            _FakeMessage([_FakeToolCall(f"c{i}", name, json.dumps(args))]) for i, (name, args) in enumerate(script)
        ]
        self.create_calls = 0
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.create_calls += 1
        return _FakeResponse(self._messages.pop(0))


PLAN_ARGS = {
    "goal": "Investigate each flagged signal for aligned and countervailing learner feedback.",
    "signals": [
        {"signal_id": sid, "aligned_plan": "search for related complaints", "countervailing_plan": "search for related praise"}
        for sid in ("S1", "S2", "S3", "S4")
    ],
}

COMPLETE_SCRIPT = [
    ("get_comment_corpus_profile", {}),
    ("list_signals", {}),
    ("submit_plan", PLAN_ARGS),
    ("search_comments", {"signal_id": "S1", "direction": "aligned", "reason": "r", "keywords": ["reply", "help"]}),
    ("search_comments", {"signal_id": "S1", "direction": "countervailing", "reason": "r", "keywords": ["detailed"]}),
    *[
        ("record_link", {"signal_id": "S1", "comment_id": cid, "relation": "aligned", "theme": "Teacher responsiveness and support", "rationale": "reports a support gap"})
        for cid in ("FB003", "FB007", "FB011")
    ],
    (
        "record_link",
        {
            "signal_id": "S1",
            "comment_id": "FB015",
            "relation": "aligned",
            "theme": "Teacher responsiveness and support",
            "rationale": "reports wanting set office hours",
            "ambiguous": True,
            "ambiguous_note": "Could be read as a general suggestion rather than a specific support complaint.",
        },
    ),
    *[
        ("record_link", {"signal_id": "S1", "comment_id": cid, "relation": "countervailing", "theme": "Teacher responsiveness and support", "rationale": "reports responsive teaching"})
        for cid in ("FB005", "FB019")
    ],
    ("search_comments", {"signal_id": "S2", "direction": "aligned", "reason": "r", "keywords": ["assessment"]}),
    ("search_comments", {"signal_id": "S2", "direction": "countervailing", "reason": "r", "keywords": ["checklist"]}),
    *[
        ("record_link", {"signal_id": "S2", "comment_id": cid, "relation": "aligned", "theme": "Assessment clarity", "rationale": "reports unclear assessment wording"})
        for cid in ("FB002", "FB009", "FB013")
    ],
    ("record_link", {"signal_id": "S2", "comment_id": "FB017", "relation": "countervailing", "theme": "Assessment clarity", "rationale": "reports clear checklists"}),
    ("search_comments", {"signal_id": "S3", "direction": "aligned", "reason": "r", "keywords": ["legal"]}),
    ("search_comments", {"signal_id": "S3", "direction": "countervailing", "reason": "r", "keywords": ["ethical"]}),
    ("note_no_evidence", {"signal_id": "S3", "reason": "No eligible comments could be linked to this unit."}),
    ("search_comments", {"signal_id": "S4", "direction": "aligned", "reason": "r", "keywords": ["withdraw"]}),
    ("search_comments", {"signal_id": "S4", "direction": "countervailing", "reason": "r", "keywords": ["flexib"]}),
    ("record_link", {"signal_id": "S4", "comment_id": "FB024", "relation": "aligned", "theme": "Flexibility and course pacing", "rationale": "reports nearly withdrawing"}),
    ("finish_investigation", {}),
]


def _make_app(tmp_path, script=None):
    app = create_app(
        data_dir=config.DATA_DIR,
        runs_dir=tmp_path,
        client=_FakeClient(script if script is not None else list(COMPLETE_SCRIPT)),
        model="fake-model",
    )
    app.config["TESTING"] = True
    return app


def _start_run(client, analyst_name="Test Analyst"):
    resp = client.post("/runs", data={"analyst_name": analyst_name}, follow_redirects=False)
    assert resp.status_code == 302
    return resp.location.rsplit("/", 1)[-1]


def _accept_all(client, run_id, finding_ids=("S1", "S2", "S3", "S4")):
    for fid in finding_ids:
        resp = client.post(f"/runs/{run_id}/review/{fid}", data={"decision": "accept", "note": ""})
        assert resp.status_code == 302


# --- basic pages -----------------------------------------------------------


def test_index_with_no_runs_redirects_to_new_run_form(tmp_path):
    app = _make_app(tmp_path)
    client = app.test_client()
    resp = client.get("/", follow_redirects=True)
    assert resp.status_code == 200
    assert b"Start a new run" in resp.data


def test_unknown_run_returns_404(tmp_path):
    app = _make_app(tmp_path)
    client = app.test_client()
    resp = client.get("/runs/does-not-exist")
    assert resp.status_code == 404


# --- full happy-path flow ---------------------------------------------------


def test_full_review_and_approve_flow(tmp_path):
    app = _make_app(tmp_path)
    client = app.test_client()

    run_id = _start_run(client, "Farley Dahl")

    resp = client.get(f"/runs/{run_id}")
    assert resp.status_code == 200
    assert b"Farley Dahl" in resp.data
    assert b"S1" in resp.data

    resp = client.get(f"/runs/{run_id}/review")
    assert resp.status_code == 200
    assert b"0 of 4 findings reviewed" in resp.data

    _accept_all(client, run_id)

    resp = client.get(f"/runs/{run_id}/review")
    assert b"4 of 4 findings reviewed" in resp.data

    resp = client.get(f"/runs/{run_id}/brief")
    assert resp.status_code == 200
    assert b"iframe" in resp.data  # brief is ready and embedded

    resp = client.get(f"/runs/{run_id}/brief.html")
    assert resp.status_code == 200
    assert b"Pending approval" in resp.data or b"Prepared by" in resp.data

    resp = client.post(f"/runs/{run_id}/approve", follow_redirects=False)
    assert resp.status_code == 302

    resp = client.get(f"/runs/{run_id}/brief.html")
    assert b"Approved for the workshop by" in resp.data
    assert b"DRAFT" not in resp.data

    state = json.loads((tmp_path / run_id / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "approved"
    assert state["pending_gate"] is None


def test_review_page_flags_ambiguous_links(tmp_path):
    app = _make_app(tmp_path)
    client = app.test_client()
    run_id = _start_run(client)

    resp = client.get(f"/runs/{run_id}/review")
    assert resp.status_code == 200
    assert b"Ambiguous" in resp.data
    assert b"Could be read as a general suggestion rather than a specific support complaint." in resp.data

    records = json.loads((tmp_path / run_id / "evidence_records.json").read_text(encoding="utf-8"))
    s1 = next(r for r in records if r["finding_id"] == "S1")
    assert s1["ambiguous_links"] == {
        "FB015": "Could be read as a general suggestion rather than a specific support complaint."
    }


def test_return_for_revision_marks_returned_not_approved(tmp_path):
    app = _make_app(tmp_path)
    client = app.test_client()
    run_id = _start_run(client)
    _accept_all(client, run_id)

    resp = client.post(f"/runs/{run_id}/return", follow_redirects=False)
    assert resp.status_code == 302

    state = json.loads((tmp_path / run_id / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == "returned"

    resp = client.get(f"/runs/{run_id}/brief.html")
    assert b"Returned for revision by" in resp.data
    assert b"Approved for the workshop by" not in resp.data

    # brief.md does use a DRAFT marker, and keeps it on a return (only an
    # approve decision removes it — see brief.update_brief_status).
    brief_md = (tmp_path / run_id / "brief.md").read_text(encoding="utf-8")
    assert "DRAFT" in brief_md
    assert "Status: Returned" in brief_md


# --- quarantined content never reaches the browser --------------------------


def test_quarantined_comment_text_never_reaches_the_browser(tmp_path):
    app = _make_app(tmp_path)
    client = app.test_client()
    run_id = _start_run(client)
    _accept_all(client, run_id)
    resp = client.post(f"/runs/{run_id}/approve")
    assert resp.status_code == 302

    for path in (
        f"/runs/{run_id}",
        f"/runs/{run_id}/review",
        f"/runs/{run_id}/activity",
        f"/runs/{run_id}/brief",
        f"/runs/{run_id}/brief.html",
    ):
        resp = client.get(path)
        body = resp.data.decode("utf-8")
        assert "FB016" not in body, path
        assert "FB018" not in body, path
        for term in FORBIDDEN_STRINGS:
            assert term not in body, f"{term!r} leaked into {path}"


def test_eligible_comments_file_never_contains_quarantined_ids(tmp_path):
    app = _make_app(tmp_path)
    client = app.test_client()
    run_id = _start_run(client)
    comments = json.loads((tmp_path / run_id / "eligible_comments.json").read_text(encoding="utf-8"))
    assert "FB016" not in comments
    assert "FB018" not in comments


# --- incomplete reviews block approval and brief drafting -------------------


def test_incomplete_review_blocks_approval(tmp_path):
    app = _make_app(tmp_path)
    client = app.test_client()
    run_id = _start_run(client)

    resp = client.post(f"/runs/{run_id}/approve")
    assert resp.status_code == 409

    # Still blocked with only some findings reviewed.
    client.post(f"/runs/{run_id}/review/S1", data={"decision": "accept", "note": ""})
    client.post(f"/runs/{run_id}/review/S2", data={"decision": "accept", "note": ""})
    resp = client.post(f"/runs/{run_id}/approve")
    assert resp.status_code == 409

    resp = client.post(f"/runs/{run_id}/return")
    assert resp.status_code == 409


def test_brief_tab_shows_not_ready_until_review_is_complete(tmp_path):
    app = _make_app(tmp_path)
    client = app.test_client()
    run_id = _start_run(client)

    resp = client.get(f"/runs/{run_id}/brief")
    assert resp.status_code == 200
    assert b"iframe" not in resp.data
    assert b"reviewed every finding" in resp.data

    resp = client.get(f"/runs/{run_id}/brief.html")
    assert resp.status_code == 404  # not generated yet


# --- duplicate submissions --------------------------------------------------


def test_duplicate_review_submission_updates_in_place(tmp_path):
    app = _make_app(tmp_path)
    client = app.test_client()
    run_id = _start_run(client)

    client.post(f"/runs/{run_id}/review/S1", data={"decision": "accept", "note": "first pass"})
    records = json.loads((tmp_path / run_id / "evidence_records.json").read_text(encoding="utf-8"))
    s1 = next(r for r in records if r["finding_id"] == "S1")
    assert s1["reviewer_status"] == "accept"
    assert s1["reviewer_note"] == "first pass"

    # Resubmitting the same finding (the analyst changed their mind) must
    # update in place, not create a second record or double-count progress.
    resp = client.post(f"/runs/{run_id}/review/S1", data={"decision": "reject", "note": "changed my mind"})
    assert resp.status_code == 302

    records = json.loads((tmp_path / run_id / "evidence_records.json").read_text(encoding="utf-8"))
    assert len(records) == 4  # still exactly one record per signal
    s1 = next(r for r in records if r["finding_id"] == "S1")
    assert s1["reviewer_status"] == "reject"
    assert s1["reviewer_note"] == "changed my mind"

    resp = client.get(f"/runs/{run_id}/review")
    assert b"1 of 4 findings reviewed" in resp.data  # not 2


def test_double_approve_is_rejected(tmp_path):
    app = _make_app(tmp_path)
    client = app.test_client()
    run_id = _start_run(client)
    _accept_all(client, run_id)

    resp = client.post(f"/runs/{run_id}/approve")
    assert resp.status_code == 302

    resp = client.post(f"/runs/{run_id}/approve")
    assert resp.status_code == 409

    resp = client.post(f"/runs/{run_id}/return")
    assert resp.status_code == 409


# --- invalid state transitions -----------------------------------------------


def test_review_submission_after_approval_is_rejected(tmp_path):
    app = _make_app(tmp_path)
    client = app.test_client()
    run_id = _start_run(client)
    _accept_all(client, run_id)
    client.post(f"/runs/{run_id}/approve")

    resp = client.post(f"/runs/{run_id}/review/S1", data={"decision": "reject", "note": "too late"})
    assert resp.status_code == 409

    # And the record itself was not changed.
    records = json.loads((tmp_path / run_id / "evidence_records.json").read_text(encoding="utf-8"))
    s1 = next(r for r in records if r["finding_id"] == "S1")
    assert s1["reviewer_status"] == "accept"


def test_review_of_unknown_finding_id_is_rejected(tmp_path):
    app = _make_app(tmp_path)
    client = app.test_client()
    run_id = _start_run(client)

    resp = client.post(f"/runs/{run_id}/review/S99", data={"decision": "accept", "note": ""})
    assert resp.status_code == 409


def test_review_with_invalid_decision_value_is_rejected(tmp_path):
    app = _make_app(tmp_path)
    client = app.test_client()
    run_id = _start_run(client)

    resp = client.post(f"/runs/{run_id}/review/S1", data={"decision": "maybe", "note": ""})
    assert resp.status_code == 409
    records = json.loads((tmp_path / run_id / "evidence_records.json").read_text(encoding="utf-8"))
    s1 = next(r for r in records if r["finding_id"] == "S1")
    assert s1["reviewer_status"] == "Pending"


def test_approve_on_unknown_run_is_404(tmp_path):
    app = _make_app(tmp_path)
    client = app.test_client()
    resp = client.post("/runs/does-not-exist/approve")
    assert resp.status_code == 404


# --- excerpt selection is recorded as an explicit analyst choice ------------


def test_excerpt_selection_recorded_from_submitted_checkboxes(tmp_path):
    app = _make_app(tmp_path)
    client = app.test_client()
    run_id = _start_run(client)

    resp = client.post(
        f"/runs/{run_id}/review/S1",
        data={
            "decision": "accept",
            "note": "",
            "aligned_excerpt": ["FB011", "FB015"],
            "countervailing_excerpt": ["FB019"],
        },
    )
    assert resp.status_code == 302

    records = json.loads((tmp_path / run_id / "evidence_records.json").read_text(encoding="utf-8"))
    s1 = next(r for r in records if r["finding_id"] == "S1")
    assert s1["aligned_excerpt_ids"] == ["FB011", "FB015"]
    assert s1["countervailing_excerpt_ids"] == ["FB019"]
    assert s1["aligned_excerpt_selection"] == "analyst selection"
    assert s1["countervailing_excerpt_selection"] == "analyst selection"


def test_excerpt_ids_not_actually_linked_are_dropped(tmp_path):
    app = _make_app(tmp_path)
    client = app.test_client()
    run_id = _start_run(client)

    resp = client.post(
        f"/runs/{run_id}/review/S1",
        data={"decision": "accept", "note": "", "aligned_excerpt": ["FB999", "FB003"]},
    )
    assert resp.status_code == 302
    records = json.loads((tmp_path / run_id / "evidence_records.json").read_text(encoding="utf-8"))
    s1 = next(r for r in records if r["finding_id"] == "S1")
    assert s1["aligned_excerpt_ids"] == ["FB003"]


# --- agent activity is built only from real audit events --------------------


def test_activity_trace_has_no_comment_text_and_no_illustrative_data(tmp_path):
    app = _make_app(tmp_path)
    client = app.test_client()
    run_id = _start_run(client)

    resp = client.get(f"/runs/{run_id}/activity")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")

    # Real search reasons/filters/result IDs from the scripted run show up...
    assert "S1" in body
    assert "reply" in body or "help" in body
    # ...but no comment text, and none of the mockup's own illustrative
    # example lines (which this app never ships).
    assert "waited over a week for a reply" not in body  # FB003's actual text
    assert "Tuesday night drop-in" not in body  # FB019's actual text
    assert "Planned the investigation" not in body  # mockup-only illustrative line


def test_activity_trace_shows_investigation_plan_exactly_once(tmp_path):
    # submit_plan produces both a "tool_call" audit event and a dedicated
    # "investigation_plan" event; the trace must render the plan from the
    # dedicated event only, not also as a generic "Called submit_plan" line.
    app = _make_app(tmp_path)
    client = app.test_client()
    run_id = _start_run(client)

    resp = client.get(f"/runs/{run_id}/activity")
    assert resp.status_code == 200
    body = resp.data.decode("utf-8")

    assert body.count(PLAN_ARGS["goal"]) == 1
    assert "Called submit_plan" not in body
