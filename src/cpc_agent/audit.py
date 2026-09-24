"""Run IDs, data/instruction versions, and the append-only audit log."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .loaders import LoadedSources


def new_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short = uuid.uuid4().hex[:8]
    return f"{timestamp}-{short}"


def input_file_hashes(sources: LoadedSources) -> dict[str, str | None]:
    return {loaded.filename: loaded.sha256 for loaded in sources.files.values() if loaded.present}


def compute_data_version(sources: LoadedSources) -> str:
    """A short, stable fingerprint of every present input file's content
    hash, used as the brief's "Input data version" so a brief can be traced
    back to the exact data/ snapshot that produced it."""
    hashes = input_file_hashes(sources)
    combined = "|".join(f"{name}:{digest}" for name, digest in sorted(hashes.items()))
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()[:12]


class AuditLog:
    """Append-only JSON-lines audit log for one run, written incrementally
    so a crash mid-run still leaves a usable trail."""

    def __init__(self, run_id: str, runs_dir: Path = config.RUNS_DIR):
        self.run_id = run_id
        self.run_dir = runs_dir / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.run_dir / "audit_log.jsonl"
        self._fh = self.path.open("a", encoding="utf-8")

    def log_event(self, event: str, **fields) -> None:
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": self.run_id,
            "event": event,
            **fields,
        }
        self._fh.write(json.dumps(record, default=str) + "\n")
        self._fh.flush()

    def log_tool_call(self, name: str, args: dict, result: object = None) -> None:
        """`result` must already be sanitised by the caller (IDs/counts
        only, never comment text) — see investigate._sanitize_result_for_log."""
        self.log_event("tool_call", tool=name, args=args, result=result)

    def close(self) -> None:
        self._fh.close()
