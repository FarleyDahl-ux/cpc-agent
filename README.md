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
Each run writes to `runs/<run_id>/`: `evidence_records.json`, `exception_log.json`, `audit_log.jsonl`, `brief.md`. Everything in `runs/` is git-ignored — it is generated output, not source.

## What's implemented vs proposed
See `docs/decisions.md` for the full design-decision log and `docs/test_results.md` for scoring against `tests/fixtures/answer_key.md` (deterministic pytest suite plus a real end-to-end run against the live OpenAI API). In short: the pipeline in `CLAUDE.md` (validation, metrics/flagging, comment screening, the LLM investigation loop, human gates, brief generation, audit log) is implemented and tested against `tests/fixtures/`. Live data connectors, pre-LLM ML-based redaction, and scheduled reruns are **proposed only** — not built.
