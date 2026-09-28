"""Local review dashboard: a Flask app for the human gates (findings
review, brief preview, final approval) described in docs/dashboard_mockup.html.

Single-user, localhost-only prototype (see run_dashboard.py, which binds
127.0.0.1) — not a production job system: "starting a run" blocks the HTTP
request for as long as the investigation stage's real LLM call takes,
there is no job queue, and state is plain JSON files with no
concurrent-write locking beyond what one dev-server process already gives
you. See docs/decisions.md for what a production version would need that
this deliberately doesn't build.

All routes read/write through dashboard_state.py, which is the only place
that touches runs/<run_id>/*.json — this module is just Flask glue.
"""
from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, abort, redirect, render_template, request, url_for

from . import config, dashboard_state
from .evidence import readable_measure_label


def create_app(
    data_dir: Path = config.DATA_DIR,
    runs_dir: Path = config.RUNS_DIR,
    client=None,
    model: str | None = None,
) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(config.TEMPLATES_DIR / "dashboard"),
        static_folder=str(config.TEMPLATES_DIR / "dashboard" / "static"),
    )
    # Only used for Flask's flash()/session machinery, neither of which this
    # prototype relies on for anything security-sensitive; a fresh random
    # key per process is fine for a single-user localhost-only dev server.
    app.config["SECRET_KEY"] = os.urandom(32)
    app.config["CPC_DATA_DIR"] = data_dir
    app.config["CPC_RUNS_DIR"] = runs_dir
    app.config["CPC_CLIENT"] = client
    app.config["CPC_MODEL"] = model

    app.jinja_env.globals["readable_label"] = readable_measure_label

    def _runs_dir() -> Path:
        return app.config["CPC_RUNS_DIR"]

    def _get_state_or_404(run_id: str) -> dict:
        state = dashboard_state.load_state(run_id, _runs_dir())
        if state is None:
            abort(404)
        return state

    def _render_run_page(template: str, state: dict, active_tab: str, **context):
        records = dashboard_state.load_evidence_records(state["run_id"], _runs_dir())
        reviewed, total = dashboard_state.review_progress(records)
        steps = dashboard_state.progress_steps(state, reviewed, total)
        waiting_title, waiting_text = dashboard_state.waiting_banner(state, reviewed, total)
        return render_template(
            template,
            state=state,
            runs=dashboard_state.list_runs(_runs_dir()),
            steps=steps,
            waiting_title=waiting_title,
            waiting_text=waiting_text,
            active_tab=active_tab,
            reviewed=reviewed,
            total=total,
            records=records,
            **context,
        )

    def _error_response(message: str, state: dict | None = None, status: int = 409):
        return (
            render_template(
                "error.html",
                message=message,
                state=state,
                runs=dashboard_state.list_runs(_runs_dir()),
                steps=[],
                waiting_title="",
                waiting_text="",
                active_tab=None,
            ),
            status,
        )

    @app.route("/")
    def index():
        runs = dashboard_state.list_runs(_runs_dir())
        if not runs:
            return redirect(url_for("new_run_form"))
        return redirect(url_for("overview", run_id=runs[0]["run_id"]))

    @app.route("/runs/new")
    def new_run_form():
        return render_template(
            "new_run.html",
            state=None,
            runs=dashboard_state.list_runs(_runs_dir()),
            steps=[],
            waiting_title="",
            waiting_text="",
            active_tab=None,
        )

    @app.route("/runs", methods=["POST"])
    def create_run():
        analyst_name = request.form.get("analyst_name", "")
        state = dashboard_state.start_run(
            analyst_name,
            data_dir=app.config["CPC_DATA_DIR"],
            runs_dir=_runs_dir(),
            client=app.config["CPC_CLIENT"],
            model=app.config["CPC_MODEL"],
        )
        return redirect(url_for("overview", run_id=state["run_id"]))

    @app.route("/runs/<run_id>")
    def overview(run_id: str):
        state = _get_state_or_404(run_id)
        exceptions = dashboard_state.load_exceptions(run_id, _runs_dir())
        trace = dashboard_state.build_activity_trace(run_id, _runs_dir())
        return _render_run_page("overview.html", state, "overview", exceptions=exceptions, trace=trace)

    @app.route("/runs/<run_id>/review")
    def review(run_id: str):
        state = _get_state_or_404(run_id)
        comments = dashboard_state.load_eligible_comments(run_id, _runs_dir())
        return _render_run_page("review.html", state, "review", comments=comments)

    @app.route("/runs/<run_id>/review/<finding_id>", methods=["POST"])
    def submit_review(run_id: str, finding_id: str):
        state = _get_state_or_404(run_id)
        decision = request.form.get("decision", "")
        note = request.form.get("note", "")
        aligned_excerpts = request.form.getlist("aligned_excerpt")
        countervailing_excerpts = request.form.getlist("countervailing_excerpt")
        ok, message = dashboard_state.apply_finding_review(
            run_id, finding_id, decision, note, aligned_excerpts, countervailing_excerpts, runs_dir=_runs_dir()
        )
        if not ok:
            return _error_response(message, state=state)
        return redirect(url_for("review", run_id=run_id))

    @app.route("/runs/<run_id>/activity")
    def activity(run_id: str):
        state = _get_state_or_404(run_id)
        trace = dashboard_state.build_activity_trace(run_id, _runs_dir())
        summary = dashboard_state.build_workflow_summary(run_id, _runs_dir())
        return _render_run_page("activity.html", state, "activity", trace=trace, summary=summary)

    @app.route("/runs/<run_id>/brief")
    def brief(run_id: str):
        state = _get_state_or_404(run_id)
        records = dashboard_state.load_evidence_records(run_id, _runs_dir())
        reviewed, total = dashboard_state.review_progress(records)
        brief_ready = total > 0 and reviewed == total
        accepted_count = sum(1 for r in records if r.get("reviewer_status") in ("accept", "edit"))
        if brief_ready:
            dashboard_state.generate_brief_preview(run_id, _runs_dir())
        return _render_run_page("brief.html", state, "brief", brief_ready=brief_ready, accepted_count=accepted_count)

    @app.route("/runs/<run_id>/brief.html")
    def brief_file(run_id: str):
        _get_state_or_404(run_id)
        path = dashboard_state.run_dir(run_id, _runs_dir()) / "brief.html"
        if not path.exists():
            abort(404)
        return path.read_text(encoding="utf-8"), 200, {"Content-Type": "text/html; charset=utf-8"}

    @app.route("/runs/<run_id>/approve", methods=["POST"])
    def approve_run(run_id: str):
        state = _get_state_or_404(run_id)
        ok, message = dashboard_state.finalize_run(run_id, "approve", runs_dir=_runs_dir())
        if not ok:
            return _error_response(message, state=state)
        return redirect(url_for("brief", run_id=run_id))

    @app.route("/runs/<run_id>/return", methods=["POST"])
    def return_run(run_id: str):
        state = _get_state_or_404(run_id)
        ok, message = dashboard_state.finalize_run(run_id, "return", runs_dir=_runs_dir())
        if not ok:
            return _error_response(message, state=state)
        return redirect(url_for("brief", run_id=run_id))

    return app
