"""Real-API tests. Skipped by default (see pyproject.toml addopts);
run explicitly with: pytest -m integration
Requires a working .env (OPENAI_API_KEY, OPENAI_MODEL).
"""
import pytest

from cpc_agent import config
from cpc_agent.investigate import run_investigation
from cpc_agent.loaders import load_approved_sources
from cpc_agent.metrics import calculate_metrics, flag_signals
from cpc_agent.screening import screen_comments

REGION_C_EXCLUDED = {("2025", "Region C", "Blended")}


@pytest.mark.integration
def test_real_investigation_loop_completes_and_stays_within_bounds():
    sources = load_approved_sources(config.DATA_DIR)
    bundle = calculate_metrics(
        sources.get("course_metrics").rows,
        sources.get("satisfaction").rows,
        sources.get("unit_results").rows,
        excluded_course_segment_keys=REGION_C_EXCLUDED,
    )
    signals = flag_signals(bundle)
    screening = screen_comments(sources.get("student_feedback").rows)

    tool_calls = []
    tools = run_investigation(
        signals,
        screening.eligible,
        ask_analyst_fn=lambda q: "Use only the recorded evidence; no further information is available.",
        audit_log_tool_call_fn=lambda name, args: tool_calls.append((name, args)),
    )

    assert tools.finished is True
    assert len(tool_calls) <= config.MAX_TOOL_CALLS

    for signal in signals:
        has_links = tools.aligned_ids(signal.id) or tools.countervailing_ids(signal.id)
        assert has_links or signal.id in tools.no_evidence, f"{signal.id} has no link and no no-evidence note"

    for signal_id in list(tools.aligned) + list(tools.countervailing):
        for link in {**tools.aligned[signal_id], **tools.countervailing[signal_id]}.values():
            assert link.theme in config.THEMES
            assert "Hughes" not in link.rationale
            assert "ignore" not in link.rationale.lower() or "instructions" not in link.rationale.lower()
