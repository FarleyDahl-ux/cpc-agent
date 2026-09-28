# CPC Preparation Agent (prototype)
Local Python agent that prepares a traceable draft Course Performance Canvas workshop brief from synthetic course data and learner feedback. People interpret the evidence and decide actions.

Build instructions for Claude Code are in `CLAUDE.md`. Design diagram: `docs/cpc_agent_flow.png`.
All data is synthetic.

## Setup
1. `python -m venv .venv` and activate it
2. `pip install -r requirements.txt`
3. `pip install -e .` (installs this package in editable mode so `cpc_agent` is importable and `python -m cpc_agent.main` works from the repo root)
4. Copy `.env.example` to `.env` and add your OpenAI API key and model name
5. `python -m cpc_agent.main` (interactive) or `python -m cpc_agent.main --answers examples/demo_answers.json` (scripted mode, for tests and repeatable demos)

## Running the tests
```
pytest
```
Unit tests are deterministic and never call the OpenAI API. Tests marked `@pytest.mark.integration` call the real API and are skipped by default; run them explicitly with `pytest -m integration` once `.env` is configured.

## Outputs
Each run writes to `runs/<run_id>/`: `evidence_records.json`, `exception_log.json`, `audit_log.jsonl`, `brief.md`, `brief.html` (a one-A4-page design brief, styled by `src/cpc_agent/templates/brief.css` — design source `docs/brief_mockup.html`, kept there only as a human-readable preview — filled entirely from that run's data, with system fonts only so it opens correctly offline), `state.json` and `eligible_comments.json`. The last of these is deleted once the run reaches its final decision (see Retention, below); `runs/retention_log.jsonl` (top-level, not per-run) records any whole run folders later purged for being too old. Everything in `runs/` is git-ignored — it is generated output, not source.

## Investigation loop

Before the model can search any feedback, it must call `submit_plan`: a one/two-sentence goal plus, for every flagged signal, its intended search approach in both directions (aligned and countervailing). `search_comments` is rejected until a plan exists, and the plan (and any later `revise_plan` call, which requires a short reason) is written to `audit_log.jsonl` — as its own `investigation_plan`/`investigation_plan_revision` event — the moment it's submitted, not reconstructed afterwards.

When the model links a comment to a signal but the reading is genuinely uncertain, it can set `record_link`'s `ambiguous` flag with a short `ambiguous_note` explaining why, instead of silently dropping the link or forcing a confident classification. The link still counts normally for evidence status; the flag and note are carried into `evidence_records.json` (`ambiguous_links`) and shown as an "Ambiguous" badge on the dashboard's review page, for a reviewer to look at more closely.

## Retention

Once a run reaches its final approval/return decision — a "return" closes the run just like an "approve" does; revising means starting a new run, not reopening this one — `eligible_comments.json` (the one *working* file a run folder holds that has raw comment text) is deleted. `brief.md` and `brief.html` are never touched: they are the approved workshop output, excerpts included, and stay exactly as generated. IDs, counts, evidence records, reviewer decisions and the audit log are all untouched too; the deletion itself is recorded in that run's `audit_log.jsonl` as a `retention_purge` event.

Separately, `python -m cpc_agent.main --purge-older-than-days N` deletes whole run folders — briefs included — older than `N` days (by each run's own embedded timestamp, not filesystem mtime) — add `--dry-run` to see what would be deleted without deleting it. Since a deleted run folder takes its own audit log with it, these deletions are recorded in a central `runs/retention_log.jsonl` instead. This is the only mechanism that ever removes a brief.

## Review dashboard

A local, single-user web dashboard for the human gates, built on `docs/dashboard_mockup.html`'s design:

```
python -m cpc_agent.run_dashboard
```

Open `http://127.0.0.1:5000` — the dashboard binds to `127.0.0.1` only and is never reachable from another machine. Use `--port` to change the port and `--debug` for Flask's reload/debug mode during development.

"Start a new run" runs the pipeline synchronously (including the real investigation-stage LLM call, so it can take up to a minute or two) using a fixed, conservative default policy for the pre-review gates — scope always confirmed, any blank-value/reconciliation/low-sample pause always excluded, no further information for `ask_analyst` — then stops for your review. This dashboard is the *smallest end-to-end version*: it doesn't build UI for those earlier gates. If you want interactive control over them, use the CLI instead (`python -m cpc_agent.main`, no `--answers`) — both share the exact same validation/metrics/screening/investigation code (`main.prepare_run_for_review`), so nothing about the pipeline itself differs between them.

From there the dashboard lets you: see each signal's value, comparison and evidence status (Overview); review each finding — choose up to two excerpt comments per side, add a note, accept/edit/reject (Review findings); see a workflow summary (goal, sources used, confirmed facts, outstanding uncertainty, approvals still required — all rolled up from the run's own record) above the run's real audit trail — tool names, search reasons/filters, result IDs and counts, never comment text (Agent activity); and preview the drafted brief and approve it or return it for revision (Brief and approval). Every human action is recorded in `audit_log.jsonl` with your name, a timestamp, the decision and any note, the same as the CLI's gates.

Not a production job system: single-user, no auth, no job queue (a run blocks the request that started it), and `state.json`/`evidence_records.json` are read and written with no concurrent-write locking beyond what one dev-server process already gives you. See `docs/decisions.md` for what a production version would need that this deliberately doesn't build.

## What's implemented vs proposed
See `docs/decisions.md` for the full design-decision log and `docs/test_results.md` for scoring against `tests/fixtures/answer_key.md` (deterministic pytest suite plus real end-to-end runs against the live OpenAI API). In short: the pipeline in `CLAUDE.md` (validation, metrics/flagging, comment screening, the LLM investigation loop, human gates, brief generation, audit log) is implemented and tested against `tests/fixtures/`, and the review dashboard (`webapp.py`/`dashboard_state.py`) implements the smallest end-to-end version of the human gates over HTTP. Live data connectors, pre-LLM ML-based redaction, and scheduled reruns remain **proposed only** — not built. So does a production-grade version of the dashboard (background job queue instead of a blocking request, concurrent-write locking or a real datastore instead of plain JSON files, multi-user auth, UI for the pre-review gates the current dashboard auto-resolves).
