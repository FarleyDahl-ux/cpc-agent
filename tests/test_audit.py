import json

from cpc_agent import config
from cpc_agent.audit import AuditLog, compute_data_version, input_file_hashes, new_run_id
from cpc_agent.loaders import load_approved_sources


def test_new_run_id_is_unique_and_sortable():
    a = new_run_id()
    b = new_run_id()
    assert a != b
    assert "T" in a and a.endswith("Z") or "-" in a


def test_input_file_hashes_only_present_files():
    sources = load_approved_sources(config.DATA_DIR)
    hashes = input_file_hashes(sources)
    assert set(hashes) == set(config.ALL_FILES.values())
    assert all(hashes.values())


def test_compute_data_version_is_stable_for_same_content():
    sources = load_approved_sources(config.DATA_DIR)
    v1 = compute_data_version(sources)
    v2 = compute_data_version(load_approved_sources(config.DATA_DIR))
    assert v1 == v2
    assert len(v1) == 12


def test_audit_log_writes_jsonl_events(tmp_path):
    log = AuditLog("test-run", runs_dir=tmp_path)
    log.log_event("run_start", instruction_version="1.0.0")
    log.log_tool_call("list_signals", {})
    log.close()

    lines = (tmp_path / "test-run" / "audit_log.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["event"] == "run_start"
    assert first["run_id"] == "test-run"
    assert "timestamp" in first
    second = json.loads(lines[1])
    assert second["event"] == "tool_call"
    assert second["tool"] == "list_signals"
