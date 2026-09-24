# Design decisions and deviations log

Format: one entry per decision, dated by build phase, with the reasoning.
Per CLAUDE.md: where the brief is ambiguous, the more conservative option is
chosen and recorded here.

## Phase 1 — Skeleton

- **Packaging (`pyproject.toml`, `pip install -e .`)**: CLAUDE.md specifies a
  `src/cpc_agent/` layout and `python -m cpc_agent.main` as the CLI entry,
  but does not specify how the package becomes importable. A bare `src/`
  directory is not on `sys.path` by default, so `python -m cpc_agent.main`
  would fail right after `pip install -r requirements.txt`. Added a minimal
  `pyproject.toml` (setuptools, src-layout package discovery, no extra
  dependencies) and documented `pip install -e .` as an additional setup
  step in the README. Also added a root `conftest.py` that inserts `src/`
  onto `sys.path` so `pytest` works even before the editable install, since
  CI/grading may run tests without that step.
- **`REQUIRED_VALUE_FIELDS` excludes `organisation_avg_pct`**: the data
  dictionary requires PAUSE when a required value is blank in a row, but
  `satisfaction.csv` legitimately leaves `organisation_avg_pct` blank for
  every 2024 row (the organisation average was only tracked from 2025). If
  that column were treated as "required value present in every row", every
  2024 row would trigger a spurious PAUSE. Treated it as required only in
  the sense that flagging logic (phase 2) will not compare against a blank
  org average — a missing org average simply removes the org-average
  flagging rule for that row, it does not halt or pause the run. This is
  the more conservative reading available: it avoids inventing/estimating a
  value while not blocking the run on an absence the data model expects.
