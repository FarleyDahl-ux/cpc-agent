import json
from datetime import datetime, timedelta, timezone

from cpc_agent import main
from cpc_agent.audit import AuditLog
from cpc_agent.retention import purge_comment_text, purge_old_runs


def _make_run(runs_dir, run_id, with_eligible_comments=True):
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True)
    log = AuditLog(run_id, runs_dir=runs_dir)
    log.log_event("run_start")
    log.close()
    if with_eligible_comments:
        (run_dir / "eligible_comments.json").write_text(
            json.dumps({"FB003": {"mode": "Online", "region": "Online", "prompt": "p", "text": "real comment text"}}),
            encoding="utf-8",
        )
    (run_dir / "evidence_records.json").write_text("[]", encoding="utf-8")
    return run_dir


# --- purge_comment_text ------------------------------------------------------


def test_purge_comment_text_deletes_the_file_and_logs_an_event(tmp_path):
    run_dir = _make_run(tmp_path, "run-a")
    ok, message = purge_comment_text("run-a", tmp_path)
    assert ok is True
    assert not (run_dir / "eligible_comments.json").exists()

    events = [json.loads(line) for line in (run_dir / "audit_log.jsonl").read_text(encoding="utf-8").splitlines()]
    purge_events = [e for e in events if e["event"] == "retention_purge"]
    assert len(purge_events) == 1
    assert purge_events[0]["target"] == "eligible_comments.json"
    assert "real comment text" not in json.dumps(purge_events[0])


def test_purge_comment_text_is_a_noop_when_nothing_to_purge(tmp_path):
    run_dir = _make_run(tmp_path, "run-b", with_eligible_comments=False)
    ok, message = purge_comment_text("run-b", tmp_path)
    assert ok is False
    assert "No eligible_comments.json" in message
    # No spurious retention_purge event when there was nothing to purge.
    events = [json.loads(line) for line in (run_dir / "audit_log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert not any(e["event"] == "retention_purge" for e in events)


def test_purge_comment_text_leaves_other_files_untouched(tmp_path):
    run_dir = _make_run(tmp_path, "run-c")
    purge_comment_text("run-c", tmp_path)
    assert (run_dir / "evidence_records.json").exists()
    assert (run_dir / "audit_log.jsonl").exists()


# --- purge_old_runs -----------------------------------------------------------


def _run_id_for(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ") + "-aaaaaaaa"


def test_purge_old_runs_deletes_only_folders_older_than_the_threshold(tmp_path):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    old_id = _run_id_for(now - timedelta(days=40))
    recent_id = _run_id_for(now - timedelta(days=2))
    _make_run(tmp_path, old_id, with_eligible_comments=False)
    _make_run(tmp_path, recent_id, with_eligible_comments=False)

    results = purge_old_runs(tmp_path, older_than_days=30, now=now)

    assert [r["run_id"] for r in results] == [old_id]
    assert results[0]["deleted"] is True
    assert not (tmp_path / old_id).exists()
    assert (tmp_path / recent_id).exists()


def test_purge_old_runs_dry_run_reports_without_deleting(tmp_path):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    old_id = _run_id_for(now - timedelta(days=40))
    _make_run(tmp_path, old_id, with_eligible_comments=False)

    results = purge_old_runs(tmp_path, older_than_days=30, dry_run=True, now=now)

    assert [r["run_id"] for r in results] == [old_id]
    assert results[0]["deleted"] is False
    assert (tmp_path / old_id).exists()  # nothing actually deleted
    assert not (tmp_path / "retention_log.jsonl").exists()  # nothing to log either


def test_purge_old_runs_records_deletions_in_a_central_retention_log(tmp_path):
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    old_id = _run_id_for(now - timedelta(days=40))
    _make_run(tmp_path, old_id, with_eligible_comments=False)

    purge_old_runs(tmp_path, older_than_days=30, now=now)

    log_path = tmp_path / "retention_log.jsonl"
    assert log_path.exists()  # survives even though the run folder itself is gone
    events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(events) == 1
    assert events[0]["event"] == "run_purged"
    assert events[0]["run_id"] == old_id
    assert events[0]["older_than_days"] == 30


def test_purge_old_runs_skips_folders_that_do_not_look_like_a_run_id(tmp_path):
    (tmp_path / "not_a_run_id").mkdir()
    (tmp_path / "not_a_run_id" / "some_file.txt").write_text("keep me", encoding="utf-8")

    results = purge_old_runs(tmp_path, older_than_days=0)

    assert results == []
    assert (tmp_path / "not_a_run_id").exists()


def test_purge_old_runs_on_missing_runs_dir_returns_empty(tmp_path):
    assert purge_old_runs(tmp_path / "does-not-exist", older_than_days=30) == []


# --- CLI wiring ---------------------------------------------------------------


def test_parse_args_accepts_purge_flags():
    args = main.parse_args(["--purge-older-than-days", "30", "--dry-run"])
    assert args.purge_older_than_days == 30
    assert args.dry_run is True
    assert args.answers is None


def test_parse_args_purge_flags_default_to_none_and_false():
    args = main.parse_args([])
    assert args.purge_older_than_days is None
    assert args.dry_run is False


def test_run_purge_old_runs_prints_a_summary_and_deletes(tmp_path, capsys):
    now = datetime.now(timezone.utc)
    old_id = _run_id_for(now - timedelta(days=40))
    _make_run(tmp_path, old_id, with_eligible_comments=False)

    result = main._run_purge_old_runs(30, dry_run=False, runs_dir=tmp_path)

    assert result == 0
    assert not (tmp_path / old_id).exists()
    out = capsys.readouterr().out
    assert "Deleted" in out
    assert old_id in out


def test_run_purge_old_runs_dry_run_prints_would_delete(tmp_path, capsys):
    now = datetime.now(timezone.utc)
    old_id = _run_id_for(now - timedelta(days=40))
    _make_run(tmp_path, old_id, with_eligible_comments=False)

    result = main._run_purge_old_runs(30, dry_run=True, runs_dir=tmp_path)

    assert result == 0
    assert (tmp_path / old_id).exists()
    out = capsys.readouterr().out
    assert "Would delete" in out


def test_run_purge_old_runs_reports_nothing_found(tmp_path, capsys):
    result = main._run_purge_old_runs(30, dry_run=False, runs_dir=tmp_path)
    assert result == 0
    assert "No run folders older than" in capsys.readouterr().out


def test_main_dispatches_to_purge_instead_of_the_pipeline_when_flag_given(monkeypatch):
    called = {}

    def fake_purge(days, dry_run):
        called["purge"] = (days, dry_run)
        return 0

    monkeypatch.setattr(main, "run_pipeline", lambda **kwargs: called.setdefault("run_pipeline", True))
    monkeypatch.setattr(main, "_run_purge_old_runs", fake_purge)
    result = main.main(["--purge-older-than-days", "7"])
    assert called == {"purge": (7, False)}
    assert "run_pipeline" not in called
    assert result == 0
