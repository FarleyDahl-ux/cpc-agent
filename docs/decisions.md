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

## Post-phase-6 hardening — regex-bypass stress tests, prompt tightening, live comparison

- **The two regex-bypass integration tests copy `data/` into a pytest
  `tmp_path` and append rows there**, never touching the real `data/`
  directory (which CLAUDE.md marks read-only, agent input only). Extra
  comment IDs `FB901`/`FB902` are deliberately picked to be checked against
  quarantine expectations (`assert "FB901" not in quarantined_ids`) without
  colliding with any real `FB0xx` ID.
- **Audit log is the one place a leak could realistically surface.**
  `EvidenceRecord` never stores per-comment rationale text (only IDs,
  themes and a generic `limitation` string — see phase 3 decision), so the
  only output that could carry a model-authored sentence quoting or
  paraphrasing a comment is `audit_log.jsonl`'s logged `record_link`/
  `note_no_evidence` tool-call arguments (`rationale`/`reason`). Both new
  tests check that file specifically for this reason, not just the brief.
- **The countervailing test in the system prompt was tightened, not
  loosened further via code.** A regex or code-level check can't judge
  whether a comment "directly contradicts" a specific measure — that's
  exactly the kind of judgement CLAUDE.md reserves for the LLM stage. The
  fix is a stricter instruction, verified empirically (5-run live
  comparison, `docs/test_results.md`), not a new deterministic filter.
- **`scripts/live_comparison.py` lives outside `src/cpc_agent/`.** It's a
  manual diagnostic for gauging live-model variance against the answer key,
  not part of the shipped pipeline (CLAUDE.md's `src/cpc_agent/` structure
  in §3 has no slot for it, and it must never run as part of `pytest`,
  since it spends real API credits on every invocation).

## Gate/brief usability pass — comment text, analyst name, readable labels, draft status

- **Full comment text at the review gate comes only from `comments_by_id`,
  built in `main.py` from `screening_result.eligible`.** Quarantined
  comments are never in that dict by construction (screening.py filters
  them out before anything downstream ever sees them), so
  `Gates._print_linked_comments` cannot leak quarantined text even in
  principle — there's no code path that could reach it, rather than a
  check that has to remember to exclude it. An aligned/countervailing ID
  with no entry in the dict (which `record_link`'s own validation should
  already prevent) prints `"(comment text unavailable)"` instead of
  crashing or guessing, which the test in `tests/test_gates.py` exercises
  directly as a defence-in-depth case.
- **The analyst's name is captured once, at the scope gate, and reused
  everywhere else** (`Gates.analyst_name`), rather than asked again at the
  findings-review or final-approval gates. `Gates` is a single instance
  threaded through the whole run in `main.py`, so this is just reading
  state set earlier in the same object — it avoids asking the same human
  who they are three times in one run. Missing/blank input defaults to
  `"(not provided)"` in both scripted and interactive mode, so a scripted
  answers file that omits `analyst_name` (e.g. an older `answers.json`)
  still runs rather than crashing.
- **`EvidenceRecord` gained a `reviewer` field** (the analyst's name),
  separate from the existing `reviewer_status`/`reviewer_note`, since none
  of those meant "who reviewed this" and `evidence_records.json` should
  carry that per CLAUDE.md §7's traceability intent. Added with a `""`
  default so it's backwards compatible with any code constructing
  `EvidenceRecord` positionally/without it (none currently does, but tests
  didn't need updating for this reason alone).
- **Bracket placeholder lines are now fully replaced, not kept as a
  leading caption.** The earlier phase-5 approach (`anchor + "\n" +
  generated`) kept the instructional bracket text from
  `data/cpc_brief_template.txt` visible above the real content in every
  section, which reads as an unfinished template even once populated.
  Changed every bracket-anchor replacement to substitute the anchor
  directly with the generated content. The two non-bracket instructional
  sentences in section 3 ("For each signal: ..." / "Feedback is reported
  experience...") are real prose, not template placeholders, and are kept.
- **DRAFT/"(not approved)" removal is decision-gated, not automatic on any
  brief write.** `populate_cpc_template` always writes the unmodified
  template title (still a draft — final approval hasn't happened yet);
  only `update_brief_status(path, "approve")` strips `DRAFT` and `(not
  approved)` from the title, via one more `str.replace` on the now-fixed
  title line. A `"return"` decision leaves the title untouched and only
  changes the Status/Decision lines, so a returned brief still reads
  clearly as an unapproved draft if it's reopened later.
- **Readable labels (`evidence.humanize_measure`/`readable_measure_label`)
  are display-only, applied at render time in `brief.py` and `gates.py`,
  never written back into `EvidenceRecord.measure`/`.segment`.** This keeps
  `evidence_records.json` machine-comparable against
  `tests/fixtures/expected_results.json` (which uses code names) while the
  brief and the interactive review prompts read naturally. Segment_
  withdrawal_rate is labelled "Withdrawal rate (segment)" rather than
  "Segment withdrawal rate (segment)" — the segment name is already in the
  parenthetical, so repeating "segment" in the label was redundant.

## brief.html — one-page design brief, built from docs/brief_mockup.html

- **The mockup's `<style>` block is read live from `docs/brief_mockup.html`
  at generation time, never hand-copied into Python.**
  `brief._load_mockup_style` slices the file between `<style>` and
  `</style>` and inlines it verbatim into every `brief.html`. If the mockup
  is redesigned later (colours, spacing, print margins), `brief.html`
  picks it up automatically with no code change — the mockup stays the
  single source of truth for how the brief looks, and CLAUDE.md's
  "populate the template" pattern (already used for `cpc_brief_template.txt`)
  extends naturally to a second, HTML-flavoured template.
- **The markup structure is generated per accepted record, not by
  substituting into the mockup's fixed 4-`<article>` body.** The mockup
  hard-codes S1–S4 because that's what this run's data happens to produce;
  a different run could flag a different number of signals (from zero up
  to CLAUDE.md's design space), so `render_brief_html` builds one
  `<article class="signal">` per accepted record in Python, reusing the
  mockup's exact class names and layout pattern (measure column + evidence
  column + footer-row) rather than treating the mockup's body as literal
  text to anchor-replace. `populate_cpc_template`'s plain-text approach
  (anchor substitution) works for `cpc_brief_template.txt` because that
  template has one fixed table/list per section; the HTML mockup's
  per-signal repetition doesn't fit that pattern.
- **`EvidenceRecord` gained `value` (the signal's raw number, e.g. 61.0)
  and `details` (a copy of `Signal.details`: org average, previous-year
  value, median unit rate, unit title, etc.), populated in
  `build_evidence_record`.** Without these, brief.py would have had to
  re-parse them out of the already-formatted `calculation`/`comparison_rule`
  strings, which is exactly the kind of hard-coding-by-another-name the
  brief asked to avoid — the gauge fill and every mark position are
  computed straight from these fields (`brief._gauge_marks`,
  `_render_gauge`), not from any per-signal template.
- **Excerpt selection is a new, explicit part of the findings review gate**
  (`EvidenceRecord.aligned_excerpt_ids`/`countervailing_excerpt_ids`, set in
  `Gates.review_findings`), not inferred automatically. `gates._resolve_excerpts`
  treats `None` as "use the default" (first two linked IDs — the lists are
  already sorted when `build_evidence_record` runs) and an explicit list
  (scripted `excerpts: {"aligned": [...], "countervailing": [...]}`, or an
  interactive comma-separated answer) as the analyst's choice; either way
  it filters to IDs that are genuinely linked to that signal and caps at
  `MAX_EXCERPTS = 2`, so an analyst (or a malformed scripted answer) can
  never make an unlinked or quarantined ID appear as an excerpt — there is
  no code path from "not in `aligned_ids`/`countervailing_ids`" to
  "rendered in the brief".
- **`brief.html` is generated once, after gate 9, unlike `brief.md`'s
  write-then-patch (`populate_cpc_template` then `update_brief_status`).**
  The HTML approval block reads differently depending on the decision
  ("Approved for the workshop by…" / "Returned for revision by…" /
  "Prepared by… Pending approval"), and by the time it's generated the
  decision is already known — a single render is simpler and avoids a
  second string-patching pass like the one `.md` needs (`.md`'s two-phase
  approach exists because its file is shown to the analyst *before* they
  decide, at gate 9 itself; `.html` is not).
- **A close-together mark pair on the gauge (within 8 percentage points)
  pushes the second label upward** (`_render_gauge`'s `bumped` check),
  mirroring the mockup's own S1 example (2024: 66% and avg 72%, 6 points
  apart, with the avg label raised). This is a presentational anti-overlap
  heuristic, not semantically tested — it only affects label placement,
  never which values are shown.
- **HTML text is escaped with `html.escape(..., quote=False)`.** Every
  value here is inserted into text content, never into an HTML attribute,
  so quotes don't need escaping; using `quote=False` keeps a comment like
  *"Not sure what 'demonstrate an understanding' means…"* rendering with
  literal apostrophes instead of `&#x27;` artifacts, while `&`/`<`/`>` are
  still escaped for safety.
- **One-A4-page scope**: the mockup's print sizing (`@page { size: A4 }`,
  point/millimetre units throughout) is preserved verbatim and unmodified,
  and the generated markup for this course's actual 4 signals reproduces
  the mockup's density almost exactly (confirmed visually against a
  locally rendered run). No auto-scaling or overflow handling was built for
  a hypothetical run with many more flagged signals than this course
  produces — CLAUDE.md's "don't add unrequested robustness" principle
  applies, and the brief is meant to summarise a handful of thresholds
  anyway (not dozens). Documented here as a known limitation rather than
  engineered around, since doing so would need real pagination logic that
  nothing in this brief asked for.

## Brief tidy-ups: plain-language exceptions, excerpt provenance, offline CSS

- **Every exception entry now carries both `detail` (technical, unchanged)
  and `summary` (plain language, new) in `exception_log.json`.** Nothing
  was removed from the JSON — `exception_log.json` stays the full
  technical record CLAUDE.md §7 specifies. Only the *brief's* rendering
  (`brief._exception_summaries`, used by both `.md` and `.html`) switched
  from `detail` to `summary`, so a human reading the brief sees "Region C
  blended delivery was excluded because the 2025 withdrawal figure was
  missing" instead of `Excluded reporting_period=2025,
  delivery_region=Region C, delivery_mode=Blended from course_metrics.csv
  — blank 'withdrawals' ...`.
- **Plain-language phrasing lives next to the domain objects that produce
  it, not in a new module**: `blank_value_summary`/`reconciliation_summary`/
  `low_sample_excluded_summary`/`low_sample_proceeded_summary` in
  `validation.py` (next to `BlankValueIssue`/`ReconciliationIssue`/
  `LowSampleIssue`), `quarantine_summary` in `screening.py` (next to
  `QuarantinedComment`). This mirrors the existing pattern of
  `readable_measure_label`/`humanize_measure` living in `evidence.py` next
  to `EvidenceRecord` — each module owns the human-facing wording for its
  own data, rather than centralising presentation logic in `main.py` or a
  new file.
- **`_segment_phrase` (validation.py) infers a segment's plain-language
  shape from which keys are present** (delivery_region+delivery_mode ->
  "Region C blended delivery"; delivery_mode+survey_item -> "Online
  teacher support results"; unit_code -> "CWSU204"), rather than one
  phrase-builder per file type. This keeps the phrasing generic enough to
  cover course_metrics.csv, satisfaction.csv and unit_results.csv issues
  without three near-duplicate functions.
- **Excerpt provenance is tracked per side, not per record.** An analyst
  can pick aligned comments explicitly while leaving countervailing to the
  default (or vice versa) in the same review turn, so
  `EvidenceRecord.aligned_excerpt_selection`/`countervailing_excerpt_selection`
  are independent fields, both defaulting to
  `evidence.AUTOMATIC_EXCERPT_SELECTION` ("automatic selection (first two
  by ID)"). `gates._resolve_excerpts` now returns `(ids, was_automatic)`
  instead of just `ids`; `was_automatic` is `True` only when the analyst's
  input was literally absent (`None` in scripted mode, a blank prompt
  answer interactively) — an explicit choice that happens to resolve to
  the same two IDs, or to nothing at all after filtering out invalid IDs,
  is still recorded as "analyst selection", because a real choice was
  made even if it didn't change the outcome.
- **The selection label is shown in `brief.html` as a small caption under
  each shown excerpt block** (`brief._excerpt_selection_note`), not in
  `brief.md`, which never shows excerpt quotes at all (only ID lists) —
  there is nothing for the label to caption there. It is always present in
  `evidence_records.json` and the `findings_review` audit log event
  either way, which is where CLAUDE.md's traceability requirement actually
  bites.
- **`src/cpc_agent/templates/brief.css` is the CSS `render_brief_html`
  actually reads** (`config.BRIEF_CSS_PATH`, resolved from the package's
  own directory via `Path(__file__)`, not `BASE_DIR`). A plain
  `packages.find` setuptools config does not bundle non-`.py` files into
  a built wheel by default, so `pyproject.toml` also declares
  `[tool.setuptools.package-data] cpc_agent = ["templates/*.css"]` —
  verified by building a real (non-editable) `pip install .` into a
  scratch venv and confirming `config.BRIEF_CSS_PATH` resolves and reads
  correctly there, not just under the editable install this repo's own
  README documents. `docs/brief_mockup.html` remains in the repo as a
  human-readable design preview (and `docs/brief_mockup.pdf` as a rendered
  reference), but generation no longer reads either file — confirmed with
  a test that moves `docs/brief_mockup.html` aside and regenerates a brief
  successfully. Future design changes should be made in `brief.css`
  directly; the mockup is a snapshot, not a live source, from this point on.
- **Google Fonts (`Public Sans`, `Source Serif 4`) were dropped from the
  font stacks, not just the `<link>` tags.** Leaving the family names in
  `font: ... "Public Sans", "Segoe UI", ...` would have been harmless
  online (the browser would just fall back to `Segoe UI` when the
  webfont isn't loaded) but pointless to keep once nothing loads them —
  removed them so the stack says what it actually uses: `"Segoe UI",
  "Helvetica Neue", Arial, sans-serif` for the sans body text and
  `Georgia, "Times New Roman", serif` for the serif quote/reading-note
  text (previously `Georgia, serif`; added the extra Windows fallback
  since `Segoe UI`'s serif counterpart isn't universal either).

## Genuinely iterative investigation: search budget, plan gating, limitations

- **`search_comments` gained required `signal_id`/`direction`/`reason`
  parameters, changing its shape from a generic corpus search to a
  per-signal, per-direction one.** The brief's "3 searches per evidence
  direction per signal" budget is only checkable at all if a search call
  declares which signal and direction it's for — without that, there is
  no way to know what to count against the cap, or to build a meaningful
  `finish_investigation` gate ("every signal searched in both
  directions"). This is a real behavioural change for the model (it must
  now decide, before searching, which direction it's investigating,
  rather than searching generically and sorting results afterwards) but
  it's what makes every other requirement in this round checkable in
  code rather than merely requested in prose.
- **Only successful searches consume the budget; rejected/errored calls
  (missing filter, over budget, duplicate) do not.** A call that never
  actually searched anything shouldn't count against a budget that exists
  to limit real search volume. The per-direction and global counters, and
  the `search_seen` dedup set, are only updated at the very end of
  `InvestigationTools.search_comments`, after every validation has passed.
- **A limitation is recorded only when a search is actually rejected for
  being over budget** (`_record_limitation` inside the per-direction and
  global budget checks), not merely when a direction happens to end at
  exactly 3 searches. A model that finds what it needs in 2 searches and
  moves on hasn't "exhausted" anything — recording a limitation there
  would misrepresent a successful, efficient search as a gap. The
  distinction is real: rejection means the model *wanted* to keep
  searching and couldn't; stopping voluntarily at or under the cap means
  it didn't need to.
- **`finish_investigation` returns `{"status": "not_finished",
  "unmet_requirements": [...]}` instead of raising `ToolError` when
  requirements aren't met.** This isn't a malformed or invalid call — it's
  a legitimate response the model is expected to read and act on, so it
  goes through the normal success path (no `error` key, still gets
  captured by `_sanitize_result_for_log` like any other result) rather
  than the error path. The loop's stop condition changed from `name ==
  "finish_investigation" and error is None` to checking `tools.finished`
  directly, since a "successful" (non-error) call can now still leave
  `finished` false.
- **The global run-wide budget (24) excuses the per-signal "both
  directions searched" requirement, but a per-signal-direction budget
  cap does not excuse the *other* direction.** `unmet_requirements()`
  returns `[]` immediately once `total_searches >= 24`, because nothing
  further can be searched no matter what — but exhausting the aligned
  budget for one signal says nothing about whether its countervailing
  direction has been searched, so that's still required (or excused only
  by the same global check). `finalize_limitations()` (called once, after
  the loop truly ends, regardless of why) is the backstop that guarantees
  every signal missing a direction's search — including ones excused only
  by the global-budget bypass, which never individually triggered a
  per-search rejection — ends up with an explicit recorded limitation
  before `evidence_records.json`/`exception_log.json` are built. Verified
  directly (`test_finalize_limitations_records_gap_for_unsearched_direction`)
  and indirectly through the no-eligible-comments early-return path,
  which also calls it.
- **The "recorded limitation" bypass in `unmet_requirements()` doesn't
  check per-signal limitations, only the global budget.** A per-direction
  budget rejection can only happen after 3 successful searches in that
  direction already exist, so it can never be the reason a direction has
  zero searches — the only way a signal-direction pair can be *unmet*
  with zero searches is if the run ends (global budget or the tool-call
  ceiling) before it's reached. Checking per-signal limitations in
  `unmet_requirements()` in addition to the global check would therefore
  be dead code; left out for that reason, not overlooked.
- **`config.MAX_TOOL_CALLS` raised from 25 to 60.** The search budget
  alone (24) was already nearly equal to the old ceiling; once
  `get_comment_corpus_profile`, `list_signals`, `record_link`/
  `note_no_evidence` per signal, and at least one (often two, given the
  new gate) `finish_investigation` call are added on top, 25 would make
  the *tool-call ceiling* the binding constraint almost every run,
  independent of whether the model was actually within its search budget
  — defeating the point of having a distinct, meaningful search budget.
  60 gives comfortable headroom (confirmed against real runs, which used
  18–24 searches plus roughly 10–15 other calls) without being large
  enough to mask a genuinely runaway loop.
- **Tool results are logged sanitised via a single function
  (`_sanitize_result_for_log`), applied uniformly to every tool, not just
  `search_comments`.** `list_signals`' result is reduced to IDs+count for
  the same reason `search_comments`' is (its dicts don't carry comment
  text, but reducing it too keeps the logging policy uniform and cheap to
  audit: "every list-of-things result becomes IDs+count" is a simpler
  invariant to verify than "every tool except these two is logged as-is").
  `get_comment_corpus_profile`, `record_link`, `note_no_evidence`,
  `ask_analyst` and `finish_investigation` results are already free of
  comment text by construction and are logged unchanged.
- **The structured `investigation_plan` event is logged once, at the end
  of `run_investigation`, not incrementally per signal or per tool call.**
  CLAUDE.md's brief for this round asked to "store a structured
  investigation plan event" (singular), and a single end-of-run summary
  (`InvestigationTools.build_plan()`) is both simpler to log/consume than
  a stream of partial updates and gives a complete picture — per-signal
  search coverage, links, no-evidence reasons and limitations — that
  partial events logged mid-run couldn't (the model can still revise a
  signal's links right up until `finish_investigation` succeeds). Routed
  through a new `audit_log_event_fn` callback (main.py wires it to
  `log.log_event`) rather than overloading `audit_log_tool_call_fn`,
  since it isn't a tool call.
- **A real 5-run live comparison after this change showed a genuine drop
  in evidence-recall consistency (documented in full in
  `docs/test_results.md`), traced to two causes: keyword-based search
  under a tight budget sometimes not surfacing a comment within 3 tries,
  and a defensible reclassification of FB024 (S4) as countervailing
  rather than aligned under the previous round's own tightened
  countervailing rule.** Neither is a code defect — the budget numbers
  (3/24/5) are exactly what the brief specified, not a judgement call
  available to loosen for better-looking recall, and the classification
  disagreement is a legitimate reading of an already-ambiguous comment
  that `answer_key.md`'s own commentary flags as boundary. Recorded
  transparently rather than adjusted away, per the brief's explicit
  instruction not to edit the answer key to match results.
