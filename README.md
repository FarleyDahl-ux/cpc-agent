# CPC Preparation Agent (prototype)
Local Python agent that prepares a traceable draft Course Performance Canvas workshop brief from synthetic course data and learner feedback. People interpret the evidence and decide actions.

Build instructions for Claude Code are in `CLAUDE.md`. Design diagram: `docs/cpc_agent_flow.png`.
All data is synthetic.

## Setup (to be completed by the build)
1. `python -m venv .venv` and activate it
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and add your OpenAI API key and model name
4. `python -m cpc_agent.main` (interactive) or `python -m cpc_agent.main --answers examples/demo_answers.json`
