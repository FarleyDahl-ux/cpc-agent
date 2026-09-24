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

## Phase 2 — Validation and metrics

- **Segment identifier for course_metrics-based signals is `delivery_region`,
  not `delivery_mode`.** In the sample data `delivery_region` values
  (Region A, Region B, Region C, Online) are already unique per row, while
  `delivery_mode` is shared by two regions ("Face to face" for both Region A
  and Region B). Using `delivery_region` as the segment label matches
  `tests/fixtures/expected_results.json` (segment "Online" for S4) and keeps
  segment and unit withdrawal signals traceable to one row.
- **"gap" for a satisfaction signal = the more negative of gap-vs-org and
  gap-vs-previous-year**, used for both the reported figure and the S1/S2
  ordering rule ("largest gap first"). Both qualifying signals in the test
  data happen to have the org-average gap as the larger one, so this reading
  reproduces `expected_results.json` exactly; it also generalises sensibly
  to a signal that only trips the previous-year rule.
- **Blank required values are excluded from `reconcile_status_counts` and
  `flag_signals` inputs, not treated as errors by those functions.** The
  PAUSE for a blank value happens once, in `find_blank_required_values`;
  downstream deterministic functions assume the analyst's exclude/abort
  decision has already been applied by the caller (gates.py, phase 5) and
  simply skip rows that are missing values, rather than re-raising.
- **`organisation_avg_pct` is never used to gate reconciliation or
  low-sample PAUSE checks** — consistent with the phase 1 decision above,
  since it is a comparison input to flagging, not a status count.
