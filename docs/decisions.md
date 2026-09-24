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

## Phase 3 — Screening and evidence status

- **A comment can carry both quarantine reasons at once** (`reasons` is a
  list, joined for the exception log). Neither trap comment in the sample
  data needs this, but a comment could plausibly contain both a name and
  injected text, and the exception log should say so rather than picking one.
- **Identifying-information regex is title-plus-capitalised-name only**
  (`Mr|Mrs|Ms|Miss|Dr` followed by 1–2 capitalised words), plus email and a
  loose digit-run phone pattern. This is deliberately narrow: a broader
  "any capitalised two-word phrase" rule would quarantine ordinary text
  (unit names, place names) and defeat the purpose of a *draft* brief the
  analyst can still act on. Documented as a known limitation — the README
  lists pre-LLM ML-based redaction as proposed, not built, for exactly this
  reason.
- **`build_evidence_record` deduplicates and sorts aligned/countervailing
  IDs and themes** before storing them, so evidence_records.json is stable
  and reproducible across runs given the same investigation output.
- **`review_required_reason` is a plain optional string parameter**, not an
  auto-detected condition. CLAUDE.md's "Review required" trigger (an
  unresolved privacy/demographic/data condition) is a judgement call about
  a specific finding; nothing in the deterministic pipeline currently
  infers it automatically, so it is left as a hook the investigation stage
  (phase 4) or a human gate can set explicitly, rather than guessed at.

## Phase 4 — LLM investigation loop

- **The loop is skipped entirely (no API call) when there are no flagged
  signals, or no eligible comments at all** (missing `student_feedback.csv`,
  or every comment quarantined). In the no-eligible-comments case every
  signal is marked "No qualitative evidence" via a deterministic
  `note_no_evidence` call rather than asking the model to discover an empty
  search result on its own. This avoids an unnecessary, costly API call for
  a quantitative-only run and keeps that path fully deterministic and
  testable without a key.
- **`record_link` treats "unknown comment_id" and "quarantined comment_id"
  identically** — both simply raise `ToolError("Unknown or quarantined
  comment_id")`. `InvestigationTools` only ever receives the *eligible*
  comments from `screening.screen_comments`, so a quarantined ID is
  indistinguishable from a nonexistent one from the tool's point of view;
  the quarantine boundary is enforced earlier, in code, per the
  architecture's "hard boundaries" principle.
- **`audit_log_tool_call_fn` receives every tool call's name and parsed
  args, including failed/malformed ones**, but is never given a tool's
  *result*. Since no tool argument schema includes raw comment text (only
  IDs, relations, themes and analyst-facing rationale/question strings),
  this guarantees the audit log can never contain quarantined or even
  eligible comment text, satisfying "tool calls (name + args, no comment
  text)" (CLAUDE.md §7) without needing a redaction pass.
- **An assistant turn with no tool calls ends the loop** (treated the same
  as `finish_investigation`, but without setting `tools.finished`), so a
  model that stops calling tools — rather than explicitly finishing —
  cannot hang the run. `main.py` (phase 5) checks `tools.finished` and
  logs a limitation if the loop ended without it.

## Phase 5 — Gates, scripted mode, brief, audit log

- **`run_pipeline` takes an optional `client`/`model` override.** The CLI
  (`main()`) never passes one, so real runs always go through
  `investigate._get_client`/`_get_model` (env-backed) as CLAUDE.md
  specifies. Tests pass a fake client directly, so the full pipeline can be
  exercised end-to-end (`tests/test_pipeline.py`) without any network
  access or API key, matching "unit tests must not call the API".
- **Blank-value and reconciliation checks run on the *unfiltered* rows**
  (`validation.reconcile_status_counts`/`check_satisfaction_sample_size`
  already skip blank cells internally), and exclusion decisions are
  collected into a single `excluded` key-set per file, applied in one
  filtering pass before `calculate_metrics`. This avoids re-deriving
  "already excluded" state across three separate PAUSE loops.
- **`brief.populate_cpc_template` edits `data/cpc_brief_template.txt` by
  anchored substring replacement**, not a templating engine — every
  placeholder token in the template (`[ ]`, `[code and title]`, the table
  separator row, etc.) is unique enough in the file to target directly.
  This keeps the shipped template itself as the single source of truth for
  section order and wording, per "fill data/cpc_brief_template.txt".
- **The brief is written once (status "Pending final approval") and then
  patched in place** (`update_brief_status`) after gate 9, rather than
  rewritten from scratch, so the Approval section reflects the analyst's
  actual decision without a second full render. This is a local file edit
  only — CLAUDE.md's "never sends, emails or publishes" is about
  distribution, not about updating the draft's own status field.
- **Workshop questions are generated by a fixed template per signal kind
  in `brief.py`, not by the LLM.** CLAUDE.md's core principle confines the
  LLM strictly to the investigation stage; brief.py runs after gate 7 using
  only accepted/edited records, so any question text must come from
  deterministic code to keep the "no recommendations / no causal claims"
  guarantee enforceable rather than model-dependent.
- **`examples/demo_answers.json` uses `"default"` keys for every gate
  table** rather than the exact blank-value issue key for Region C. In this
  dataset only one segment ever pauses, so "exclude the Region C segment"
  and "exclude whatever blank value comes up" are equivalent, and the
  generic form is easier to keep correct if the sample data changes.

## Phase 6 — End-to-end run and scoring

- **`pytest`'s default run excludes `@pytest.mark.integration` tests** via
  `addopts = "-m 'not integration'"` in `pyproject.toml`, rather than
  relying on contributors to remember `-m "not integration"` by hand. Run
  them explicitly with `pytest -m integration`.
- **`_create_completion` retries once with `reasoning_effort="none"`** if
  the API rejects the first call with a "reasoning_effort ... not
  supported" error. Discovered running the real end-to-end test against
  the configured model (`gpt-6-sol`): OpenAI's chat.completions endpoint
  refuses function tools with its default reasoning-effort setting for
  that model and asks for either the `/v1/responses` endpoint or
  `reasoning_effort='none'`. Kept the simpler fix (stay on
  chat.completions, matching CLAUDE.md's "plain agent loop" function
  calling) rather than switching endpoints, since it is model-specific and
  harmless for models that don't need it (the retry only triggers on that
  exact error message).
- **`record_link`'s `theme` argument is now constrained to
  `config.THEMES`**, via the tool's JSON-schema `enum` and a matching
  check in `InvestigationTools.record_link`. The first real end-to-end run
  showed the live model inventing free-text theme labels (e.g. "Access to
  live help") in the absence of any constraint — the system prompt only
  said "a theme", and `data_dictionary.txt`'s fixed theme list was never
  actually enforced anywhere. Documented in `docs/test_results.md` as the
  one correctness gap the live run surfaced that the mocked test suite,
  built around already-canonical theme strings, could not have caught.
- **A real live run's evidence-status output can differ from
  `tests/fixtures/expected_results.json`/`answer_key.md`** even though the
  deterministic evidence-status *code* matches them exactly given the
  answer key's own aligned/countervailing IDs (proven in
  `tests/test_evidence.py`). In one real run the model linked FB022 as
  countervailing for S4, which the answer key doesn't include, changing
  S4's status from Limited to Mixed via the same (correct) precedence
  rule. This is expected LLM judgement variance in the investigation
  stage, not a defect — recorded transparently in
  `docs/test_results.md` rather than treated as a test failure, since
  CLAUDE.md's architecture deliberately keeps calculation/flagging/status
  deterministic while leaving comment interpretation to the model.
