"""Comment-text retention policy.

Two independent mechanisms:

- purge_comment_text(run_id, runs_dir): called once, immediately after a
  run's final approval/return decision. Deletes eligible_comments.json —
  the only file a run folder ever writes that holds raw comment text —
  and is logged as its own event in that run's own audit_log.jsonl (still
  present; only the comment text goes). IDs, counts, evidence records,
  reviewer decisions and the audit log itself are all untouched. Callers
  are responsible for re-rendering brief.html afterwards (see
  main.run_pipeline / dashboard_state.finalize_run) so the excerpt
  blockquotes it had already embedded don't keep surviving on disk as the
  one other place comment text could linger.

- purge_old_runs(runs_dir, older_than_days): a CLI-triggered sweep
  (`--purge-older-than-days`) that deletes whole run folders once they
  are older than the configured threshold. A run's age is read from the
  UTC timestamp embedded in its own run_id (see audit.new_run_id) rather
  than filesystem mtimes, which can change on copy/backup and wouldn't
  work for a run folder that has no other timestamped file. Because the
  whole folder — including that run's own audit_log.jsonl — is removed,
  every deletion is recorded in a central, run-independent
  runs/retention_log.jsonl instead.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config
from .audit import AuditLog

RUN_ID_TIMESTAMP_FORMAT = "%Y%m%dT%H%M%SZ"


def purge_comment_text(run_id: str, runs_dir: Path = config.RUNS_DIR) -> tuple[bool, str]:
    """Deletes runs/<run_id>/eligible_comments.json, if present, and logs
    the deletion to that run's audit_log.jsonl. Returns (ok, message); ok
    is False (a no-op, not an error) if there was nothing to purge —
    e.g. a CLI-only run, which never had a feedback file, or a run
    already purged once."""
    path = runs_dir / run_id / "eligible_comments.json"
    if not path.exists():
        return False, "No eligible_comments.json to purge for this run."
    path.unlink()

    log = AuditLog(run_id, runs_dir=runs_dir)
    log.log_event(
        "retention_purge",
        target="eligible_comments.json",
        reason="Comment text purged after the final approval/return decision.",
    )
    log.close()
    return True, "Comment text purged."


def _run_created_at(run_id: str) -> datetime | None:
    """Parses the UTC timestamp embedded in a run_id's prefix
    ("<YYYYMMDDTHHMMSSZ>-<8 hex chars>", see audit.new_run_id). Returns
    None for a folder name that doesn't match that shape — such a folder
    is never touched by purge_old_runs, since there's no reliable
    evidence of its age."""
    prefix = run_id.split("-", 1)[0]
    try:
        return datetime.strptime(prefix, RUN_ID_TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _log_run_purged(runs_dir: Path, run_id: str, created_at: datetime, older_than_days: int) -> None:
    """Appends one line to runs/retention_log.jsonl — a central, run-
    independent ledger, since the run folder (and its own audit_log.jsonl)
    no longer exists once this fires."""
    path = runs_dir / "retention_log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": "run_purged",
        "run_id": run_id,
        "run_created_at": created_at.isoformat(),
        "older_than_days": older_than_days,
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


def purge_old_runs(
    runs_dir: Path,
    older_than_days: int,
    dry_run: bool = False,
    now: datetime | None = None,
) -> list[dict]:
    """Deletes every run folder under runs_dir whose run_id timestamp is
    older than older_than_days. Returns one {"run_id", "created_at",
    "deleted"} entry per folder considered old enough, whether or not
    dry_run actually deleted it, so --dry-run has something to report."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=older_than_days)
    results: list[dict] = []
    if not runs_dir.exists():
        return results
    for entry in sorted(runs_dir.iterdir()):
        if not entry.is_dir():
            continue
        created_at = _run_created_at(entry.name)
        if created_at is None or created_at >= cutoff:
            continue
        result = {"run_id": entry.name, "created_at": created_at.isoformat(), "deleted": False}
        if not dry_run:
            shutil.rmtree(entry)
            result["deleted"] = True
            _log_run_purged(runs_dir, entry.name, created_at, older_than_days)
        results.append(result)
    return results
