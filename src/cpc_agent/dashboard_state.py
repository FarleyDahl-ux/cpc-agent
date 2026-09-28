"""Persistence and orchestration for the review dashboard.

A local, single-user prototype: state is plain JSON files under
runs/<run_id>/, read and written synchronously with no locking beyond
what a single-process dev server already gives you. Not a production job
system — see docs/decisions.md for what that would need that this
deliberately doesn't build (a job queue, concurrent-write locking,
multi-user auth).

state.json tracks the run's status and which gate (if any) is pending;
evidence_records.json/exception_log.json/audit_log.jsonl stay in the exact
shape CLAUDE.md §7 specifies, so the CLI and this dashboard can both read
and write them. eligible_comments.json (written by
main.prepare_run_for_review) is the one dashboard-only file: comment text
for every *eligible* comment, keyed by ID — quarantined comments are never
in it, so there is no code path from it to the browser for quarantined
text.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import config, retention
from .audit import AuditLog
from .brief import populate_cpc_template, render_brief_html, update_brief_status
from .evidence import EvidenceRecord, readable_measure_label
from .gates import _excerpt_selection_label, _resolve_excerpts
from .main import prepare_run_for_review
from .screening import EligibleComment
from .validation import ScopeContext

STATUS_REVIEW = "review_findings"
STATUS_BRIEF_READY = "brief_ready"
STATUS_APPROVED = "approved"
STATUS_RETURNED = "returned"
STATUS_HALTED = "halted"
STATUS_ABORTED = "aborted"

DEFAULT_ASK_ANALYST_ANSWER = (
    "No further information is available beyond the recorded evidence; use judgement and note the limitation."
)


# --- file paths -----------------------------------------------------------


def run_dir(run_id: str, runs_dir: Path = config.RUNS_DIR) -> Path:
    return runs_dir / run_id


def state_path(run_id: str, runs_dir: Path = config.RUNS_DIR) -> Path:
    return run_dir(run_id, runs_dir) / "state.json"


# --- reading and writing state ---------------------------------------------


def load_state(run_id: str, runs_dir: Path = config.RUNS_DIR) -> dict | None:
    path = state_path(run_id, runs_dir)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(state: dict, runs_dir: Path = config.RUNS_DIR) -> None:
    path = state_path(state["run_id"], runs_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def list_runs(runs_dir: Path = config.RUNS_DIR) -> list[dict]:
    if not runs_dir.exists():
        return []
    runs = []
    for entry in sorted(runs_dir.iterdir()):
        if not entry.is_dir():
            continue
        path = entry / "state.json"
        if not path.exists():
            continue
        try:
            runs.append(json.loads(path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            continue
    runs.sort(key=lambda s: s.get("created_at", ""), reverse=True)
    return runs


def load_evidence_records(run_id: str, runs_dir: Path = config.RUNS_DIR) -> list[dict]:
    path = run_dir(run_id, runs_dir) / "evidence_records.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def save_evidence_records(run_id: str, records: list[dict], runs_dir: Path = config.RUNS_DIR) -> None:
    path = run_dir(run_id, runs_dir) / "evidence_records.json"
    path.write_text(json.dumps(records, indent=2), encoding="utf-8")


def load_exceptions(run_id: str, runs_dir: Path = config.RUNS_DIR) -> dict:
    path = run_dir(run_id, runs_dir) / "exception_log.json"
    if not path.exists():
        return {"pauses": [], "exclusions": [], "quarantined": [], "limitations": []}
    return json.loads(path.read_text(encoding="utf-8"))


def load_eligible_comments(run_id: str, runs_dir: Path = config.RUNS_DIR) -> dict:
    path = run_dir(run_id, runs_dir) / "eligible_comments.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def load_audit_events(run_id: str, runs_dir: Path = config.RUNS_DIR) -> list[dict]:
    path = run_dir(run_id, runs_dir) / "audit_log.jsonl"
    if not path.exists():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def _reconstruct_context(state: dict) -> ScopeContext:
    return ScopeContext(
        course_code=state.get("course_code", config.COURSE_CODE),
        course_title=state.get("course_title", config.COURSE_TITLE),
        current_period=state.get("current_period", config.CURRENT_PERIOD),
        previous_period=state.get("previous_period", config.PREVIOUS_PERIOD),
        files_found=[tuple(item) for item in state.get("files_found", [])],
    )


def _reconstruct_comments(run_id: str, state: dict, runs_dir: Path = config.RUNS_DIR) -> dict[str, EligibleComment]:
    raw = load_eligible_comments(run_id, runs_dir)
    period = state.get("current_period", config.CURRENT_PERIOD)
    return {
        cid: EligibleComment(
            comment_id=cid,
            reporting_period=period,
            delivery_region=c.get("region", ""),
            delivery_mode=c.get("mode", ""),
            prompt=c.get("prompt", ""),
            text=c.get("text", ""),
        )
        for cid, c in raw.items()
    }


# --- starting a run ---------------------------------------------------------


def start_run(
    analyst_name: str,
    data_dir: Path = config.DATA_DIR,
    runs_dir: Path = config.RUNS_DIR,
    client=None,
    model: str | None = None,
) -> dict:
    """Starts a new run synchronously (including the real investigation
    call), auto-resolving every pre-review gate with a fixed, conservative
    policy: exclude on any blank-value/reconciliation/low-sample pause, no
    further information for ask_analyst. This dashboard's "smallest
    end-to-end version" doesn't build UI for those gates — the CLI remains
    available for an analyst who wants interactive control over them (see
    docs/decisions.md). Returns the resulting state dict."""
    analyst_name = (analyst_name or "").strip() or "(not provided)"
    answers = {
        "scope_confirm": True,
        "analyst_name": analyst_name,
        "blank_value": {"default": "exclude"},
        "reconciliation": {"default": "exclude"},
        "low_sample": {"default": "exclude"},
        "ask_analyst": {"default": DEFAULT_ASK_ANALYST_ANSWER},
    }
    prep = prepare_run_for_review(answers=answers, data_dir=data_dir, runs_dir=runs_dir, client=client, model=model)

    if prep.status == "halted":
        status = STATUS_HALTED
    elif prep.status == "aborted":
        status = STATUS_ABORTED
    else:
        status = STATUS_REVIEW

    state = {
        "run_id": prep.run_id,
        "status": status,
        "pending_gate": "findings_review" if prep.status == "ready" else None,
        "message": prep.message,
        "analyst_name": analyst_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if prep.status == "ready":
        state.update(
            {
                "course_code": prep.context.course_code,
                "course_title": prep.context.course_title,
                "current_period": prep.context.current_period,
                "previous_period": prep.context.previous_period,
                "files_found": [list(item) for item in prep.context.files_found],
                "data_version": prep.data_version,
                "snapshot_date": prep.snapshot_date,
                "finding_ids": [s.id for s in prep.signals],
            }
        )
    save_state(state, runs_dir)
    return state


# --- reviewing a finding -----------------------------------------------------


def review_progress(records: list[dict]) -> tuple[int, int]:
    total = len(records)
    reviewed = sum(1 for r in records if r.get("reviewer_status") != "Pending")
    return reviewed, total


def apply_finding_review(
    run_id: str,
    finding_id: str,
    decision: str,
    note: str,
    aligned_excerpt_ids: list[str],
    countervailing_excerpt_ids: list[str],
    runs_dir: Path = config.RUNS_DIR,
) -> tuple[bool, str]:
    """Applies one finding's review decision. Returns (ok, message); never
    partially applies a rejected request. The excerpt lists from the web
    form are always treated as an explicit choice (the analyst has seen
    and submitted them, even if they left the pre-ticked defaults as-is),
    never as "no choice made" — unlike the CLI/scripted answers file,
    where an absent key genuinely means no answer was given."""
    state = load_state(run_id, runs_dir)
    if state is None:
        return False, "Run not found."
    if state["status"] != STATUS_REVIEW:
        return False, f"This run is not awaiting findings review (status: {state['status']})."
    if decision not in ("accept", "edit", "reject"):
        return False, f"Invalid decision: {decision!r}."

    records = load_evidence_records(run_id, runs_dir)
    record = next((r for r in records if r["finding_id"] == finding_id), None)
    if record is None:
        return False, f"Unknown finding: {finding_id!r}."

    aligned_resolved, aligned_auto = _resolve_excerpts(list(aligned_excerpt_ids), record["aligned_ids"])
    countervailing_resolved, countervailing_auto = _resolve_excerpts(
        list(countervailing_excerpt_ids), record["countervailing_ids"]
    )

    record["reviewer"] = state.get("analyst_name", "(not provided)")
    record["reviewer_status"] = decision
    record["reviewer_note"] = note or ""
    record["aligned_excerpt_ids"] = aligned_resolved
    record["countervailing_excerpt_ids"] = countervailing_resolved
    record["aligned_excerpt_selection"] = _excerpt_selection_label(aligned_auto)
    record["countervailing_excerpt_selection"] = _excerpt_selection_label(countervailing_auto)

    save_evidence_records(run_id, records, runs_dir)

    log = AuditLog(run_id, runs_dir=runs_dir)
    log.log_event(
        "gate",
        gate="findings_review",
        finding_id=finding_id,
        analyst=record["reviewer"],
        decision=decision,
        note=note or "",
        aligned_excerpt_ids=aligned_resolved,
        aligned_excerpt_selection=record["aligned_excerpt_selection"],
        countervailing_excerpt_ids=countervailing_resolved,
        countervailing_excerpt_selection=record["countervailing_excerpt_selection"],
    )
    log.close()

    reviewed, total = review_progress(records)
    if reviewed == total and state["status"] == STATUS_REVIEW:
        state["status"] = STATUS_BRIEF_READY
        state["pending_gate"] = "final_approval"
        save_state(state, runs_dir)

    return True, "Saved."


# --- brief preview and final approval ---------------------------------------


def generate_brief_preview(run_id: str, runs_dir: Path = config.RUNS_DIR) -> tuple[bool, str]:
    """(Re)writes brief.md/brief.html from the current evidence records.
    Safe to call repeatedly while a run is still pending its final
    decision (e.g. on every GET of the brief tab) — always reflects the
    latest accepted/edited findings, and decision reads "pending" until
    finalize_run records the real one. Callers must stop calling this once
    a run is approved/returned — see webapp.brief()'s status guard — so
    that the finalised brief (excerpts included) is never rewritten again,
    in particular not after retention.purge_comment_text removes
    eligible_comments.json."""
    state = load_state(run_id, runs_dir)
    if state is None:
        return False, "Run not found."
    if state["status"] not in (STATUS_BRIEF_READY, STATUS_APPROVED, STATUS_RETURNED):
        return False, "Findings review is not complete yet."

    records = load_evidence_records(run_id, runs_dir)
    accepted = [EvidenceRecord(**r) for r in records if r["reviewer_status"] in ("accept", "edit")]
    context = _reconstruct_context(state)
    exceptions = load_exceptions(run_id, runs_dir)
    comments_by_id = _reconstruct_comments(run_id, state, runs_dir)
    generated_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    target_dir = run_dir(run_id, runs_dir)

    populate_cpc_template(
        accepted, context, run_id, state.get("data_version", ""), config.INSTRUCTION_VERSION,
        generated_date, state.get("snapshot_date", "n/a"), exceptions, target_dir,
        analyst_name=state.get("analyst_name", "(not provided)"),
    )
    decision = "approve" if state["status"] == STATUS_APPROVED else "return" if state["status"] == STATUS_RETURNED else "pending"
    render_brief_html(
        accepted, context, run_id, state.get("data_version", ""), generated_date,
        state.get("snapshot_date", "n/a"), exceptions, state.get("analyst_name", "(not provided)"),
        decision, comments_by_id, target_dir,
    )
    if decision != "pending":
        update_brief_status(target_dir / "brief.md", decision)
    return True, "Brief drafted."


def finalize_run(run_id: str, decision: str, runs_dir: Path = config.RUNS_DIR) -> tuple[bool, str]:
    """Records the final approval/return decision. Returns (ok, message);
    rejects if review isn't complete or the run has already been finalised
    — a final decision, once recorded, cannot be silently resubmitted."""
    if decision not in ("approve", "return"):
        return False, f"Invalid decision: {decision!r}."
    state = load_state(run_id, runs_dir)
    if state is None:
        return False, "Run not found."
    if state["status"] != STATUS_BRIEF_READY:
        return False, f"This run is not awaiting final approval (status: {state['status']})."

    records = load_evidence_records(run_id, runs_dir)
    reviewed, total = review_progress(records)
    if reviewed != total:
        return False, "All findings must be reviewed before final approval."

    state["status"] = STATUS_APPROVED if decision == "approve" else STATUS_RETURNED
    state["pending_gate"] = None
    save_state(state, runs_dir)

    ok, message = generate_brief_preview(run_id, runs_dir)
    if not ok:  # pragma: no cover — defensive; state was just validated above
        return False, message

    target_dir = run_dir(run_id, runs_dir)
    log = AuditLog(run_id, runs_dir=runs_dir)
    log.log_event(
        "gate", gate="final_approval", analyst=state.get("analyst_name", "(not provided)"),
        decision=decision, brief_path=str(target_dir / "brief.md"),
    )
    log.log_event("run_end", status="completed", final_decision=decision)
    log.close()

    # brief.md/brief.html (written above, excerpts included) are the
    # approved workshop output and are never touched by retention — only
    # the working file that held raw comment text is removed. A "return"
    # closes this run exactly like an "approve" does; a revision means
    # starting a new run, not reopening this one.
    retention.purge_comment_text(run_id, runs_dir)

    return True, f"Run {decision}d."


# --- agent activity trace ----------------------------------------------
#
# Built only from real audit_log.jsonl events — tool names, search
# reasons/filters, result IDs and counts (already sanitised when logged,
# see investigate._sanitize_result_for_log) — never comment text, and
# never a hardcoded/illustrative example. A run with no events yet just
# produces an empty trace.


def _fmt_time(timestamp: str) -> str:
    try:
        return datetime.fromisoformat(timestamp).strftime("%H:%M:%S")
    except ValueError:
        return timestamp


def _search_filter_summary(args: dict) -> str:
    keywords = args.get("keywords") or []
    parts = [p for p in [args.get("delivery_mode"), args.get("delivery_region"), *keywords] if p]
    return ", ".join(parts) if parts else "no filter"


def _trace_entry_for_tool_call(time: str, event: dict) -> dict | None:
    tool = event.get("tool")
    args = event.get("args") or {}
    result = event.get("result")
    result = result if isinstance(result, dict) else {}

    if tool in ("submit_plan", "revise_plan"):
        # Already shown as a dedicated "investigation_plan"/
        # "investigation_plan_revision" event (logged the moment the plan
        # is submitted/revised, see investigate.run_investigation) — skip
        # the generic tool_call entry so the plan doesn't appear twice.
        return None
    if tool == "search_comments":
        if "error" in result:
            return {"time": time, "kind": "agent", "what": "Search rejected", "why": result.get("error", "")}
        return {
            "time": time,
            "kind": "agent",
            "what": f"Searched {args.get('direction', '?')} comments for {args.get('signal_id', '?')}",
            "why": f"{args.get('reason', '')} — filters: {_search_filter_summary(args)}",
            "res": f"{result.get('count', 0)} result(s): {', '.join(result.get('result_ids', []))}"
            if result.get("result_ids")
            else f"{result.get('count', 0)} result(s)",
        }
    if tool == "get_comment_corpus_profile":
        return {"time": time, "kind": "agent", "what": "Checked what feedback is available"}
    if tool == "list_signals":
        return {"time": time, "kind": "agent", "what": "Listed the flagged signals"}
    if tool == "record_link":
        if "error" in result:
            return {"time": time, "kind": "agent", "what": "Link rejected", "why": result.get("error", "")}
        return {
            "time": time,
            "kind": "agent",
            "what": f"Linked {args.get('comment_id', '?')} as {args.get('relation', '?')} to {args.get('signal_id', '?')}",
            "why": args.get("theme", ""),
        }
    if tool == "note_no_evidence":
        if "error" in result:
            return {"time": time, "kind": "agent", "what": "No-evidence note rejected", "why": result.get("error", "")}
        return {
            "time": time,
            "kind": "agent",
            "what": f"Recorded no evidence for {args.get('signal_id', '?')}",
            "why": args.get("reason", ""),
        }
    if tool == "ask_analyst":
        return {"time": time, "kind": "agent", "what": "Asked the analyst a question", "why": args.get("question", "")}
    if tool == "finish_investigation":
        if result.get("status") == "not_finished":
            return {
                "time": time,
                "kind": "agent",
                "what": "Tried to finish investigating — not ready yet",
                "res": "; ".join(result.get("unmet_requirements", [])),
            }
        return {"time": time, "kind": "agent", "what": "Finished investigating"}
    return {"time": time, "kind": "agent", "what": f"Called {tool}"}


def _trace_entry(event: dict) -> dict | None:
    kind = event.get("event")
    time = _fmt_time(event.get("timestamp", ""))
    analyst = event.get("analyst") or "You"

    if kind == "run_start":
        return {"time": time, "kind": "sys", "what": "Run started"}
    if kind == "gate":
        gate = event.get("gate")
        if gate == "scope_confirm":
            return {"time": time, "kind": "human", "what": f"{analyst} confirmed the scope"}
        if gate in ("blank_value", "reconciliation", "low_sample"):
            return {
                "time": time,
                "kind": "human",
                "what": f"Resolved a data pause ({gate.replace('_', ' ')})",
                "why": f"Decision: {event.get('decision')}",
            }
        if gate == "ask_analyst":
            return {"time": time, "kind": "human", "what": "Answered the agent's question", "why": event.get("question", "")}
        if gate == "findings_review":
            entry = {
                "time": time,
                "kind": "human",
                "what": f"{analyst} reviewed {event.get('finding_id')}: {event.get('decision')}",
            }
            if event.get("note"):
                entry["why"] = event["note"]
            return entry
        if gate == "final_approval":
            return {"time": time, "kind": "human", "what": f"{analyst} {event.get('decision')}d the run"}
        return None
    if kind == "quarantine":
        return {"time": time, "kind": "sys", "what": "Held back a comment from review", "res": event.get("reason", "")}
    if kind == "exclusion":
        return {"time": time, "kind": "sys", "what": "Excluded a segment", "res": event.get("summary", "")}
    if kind in ("limitation", "investigation_limitation", "low_sample_proceeded"):
        return {"time": time, "kind": "sys", "what": "Recorded a limitation", "res": event.get("summary", "")}
    if kind == "signals_flagged":
        count = len(event.get("signals", []))
        return {"time": time, "kind": "sys", "what": f"Flagged {count} result{'s' if count != 1 else ''}"}
    if kind == "investigation_plan":
        signal_count = len(event.get("signals", []))
        return {
            "time": time,
            "kind": "sys",
            "what": "Submitted an investigation plan",
            "why": event.get("goal", ""),
            "res": f"Covers {signal_count} signal{'s' if signal_count != 1 else ''}",
        }
    if kind == "investigation_plan_revision":
        signal_count = len(event.get("signals", []))
        return {
            "time": time,
            "kind": "sys",
            "what": "Revised the investigation plan",
            "why": event.get("reason", ""),
            "res": f"Covers {signal_count} signal{'s' if signal_count != 1 else ''}",
        }
    if kind == "tool_call":
        return _trace_entry_for_tool_call(time, event)
    if kind in ("brief_written", "brief_html_written"):
        return {"time": time, "kind": "sys", "what": "Drafted the brief"}
    if kind == "halt":
        return {"time": time, "kind": "sys", "what": "Halted", "res": event.get("reason", "")}
    if kind == "run_end":
        entry = {"time": time, "kind": "sys", "what": f"Run {event.get('status', 'ended')}"}
        if event.get("final_decision"):
            entry["res"] = f"Final decision: {event['final_decision']}"
        return entry
    return None


def build_activity_trace(run_id: str, runs_dir: Path = config.RUNS_DIR) -> list[dict]:
    trace = []
    for event in load_audit_events(run_id, runs_dir):
        entry = _trace_entry(event)
        if entry is not None:
            trace.append(entry)
    return trace


# --- workflow summary panel ---------------------------------------------
#
# A single read-only snapshot for the top of the Agent Activity tab, built
# only from this run's own record (state.json/evidence_records.json/
# exception_log.json/audit_log.jsonl) — never from a live InvestigationTools
# instance, and never any comment text. Answers, at a glance: what was this
# run trying to establish, what did it actually look at, what did it find
# (with figures), what's still uncertain, and what's still waiting on a
# human — the same four questions build_activity_trace's event-by-event
# list answers, just rolled up instead of chronological.


def _workflow_goal(events: list[dict]) -> str:
    """The most recent submit_plan/revise_plan goal — a revision fully
    replaces the plan (see investigate.InvestigationTools.revise_plan), so
    the latest one is the model's current stated goal, not the first."""
    for event in reversed(events):
        if event.get("event") in ("investigation_plan", "investigation_plan_revision"):
            return event.get("goal", "")
    return ""


def _workflow_sources(state: dict) -> list[str]:
    return [filename for _key, filename, present in (tuple(item) for item in state.get("files_found", [])) if present]


def _workflow_confirmed_facts(records: list[dict]) -> list[str]:
    return [
        f"{r['finding_id']}: {readable_measure_label(r['measure'], r['segment'])} — {r['calculation']} ({r['comparison_rule']})"
        for r in records
    ]


def _workflow_uncertainty(records: list[dict], exceptions: dict) -> list[str]:
    items = [entry.get("summary", entry.get("detail", "")) for entry in exceptions.get("limitations", [])]
    for r in records:
        for comment_id, note in r.get("ambiguous_links", {}).items():
            items.append(f"{r['finding_id']}: {comment_id} is ambiguous — {note}")
    return items


def _workflow_approvals_required(state: dict, reviewed: int, total: int) -> list[str]:
    status = state.get("status")
    if status == STATUS_REVIEW:
        return [f"Findings review ({reviewed}/{total} reviewed)"]
    if status == STATUS_BRIEF_READY:
        return ["Final approval"]
    return []


def build_workflow_summary(run_id: str, runs_dir: Path = config.RUNS_DIR) -> dict:
    state = load_state(run_id, runs_dir) or {}
    records = load_evidence_records(run_id, runs_dir)
    exceptions = load_exceptions(run_id, runs_dir)
    events = load_audit_events(run_id, runs_dir)
    reviewed, total = review_progress(records)

    return {
        "goal": _workflow_goal(events),
        "sources": _workflow_sources(state),
        "confirmed_facts": _workflow_confirmed_facts(records),
        "uncertainty": _workflow_uncertainty(records, exceptions),
        "approvals_required": _workflow_approvals_required(state, reviewed, total),
    }


# --- sidebar progress / top banner ------------------------------------


def progress_steps(state: dict, reviewed: int, total: int) -> list[dict]:
    status = state.get("status")
    if status in (STATUS_HALTED, STATUS_ABORTED):
        return [{"label": "Run could not start", "cls": "todo"}]
    steps = [{"label": "Data validated and investigated", "cls": "human done"}]
    if status == STATUS_REVIEW:
        steps.append({"label": f"Review findings ({reviewed}/{total})", "cls": "now"})
        steps.append({"label": "Brief drafted", "cls": "todo"})
        steps.append({"label": "Final approval", "cls": "todo"})
    elif status == STATUS_BRIEF_READY:
        steps.append({"label": "Review findings", "cls": "human done"})
        steps.append({"label": "Brief drafted", "cls": "done"})
        steps.append({"label": "Final approval", "cls": "now"})
    else:  # approved / returned
        steps.append({"label": "Review findings", "cls": "human done"})
        steps.append({"label": "Brief drafted", "cls": "done"})
        steps.append({"label": "Final approval", "cls": "human done"})
    return steps


def waiting_banner(state: dict, reviewed: int, total: int) -> tuple[str, str]:
    status = state.get("status")
    analyst = state.get("analyst_name", "(not provided)")
    if status == STATUS_HALTED:
        return "Halted", state.get("message", "")
    if status == STATUS_ABORTED:
        return "Aborted", state.get("message", "")
    if status == STATUS_REVIEW:
        remaining = total - reviewed
        return "Waiting for you", f"Review {remaining} more finding{'s' if remaining != 1 else ''} before the brief can be drafted."
    if status == STATUS_BRIEF_READY:
        return "Waiting for you", "The brief is drafted and needs your final approval."
    if status == STATUS_APPROVED:
        return "Approved", f"Approved by {analyst}. Nothing has been sent."
    if status == STATUS_RETURNED:
        return "Returned", f"Returned for revision by {analyst}."
    return "", ""
