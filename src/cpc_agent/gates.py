"""Human approval gates.

Interactive mode reads answers from the keyboard; scripted mode (--answers
answers.json) reads them from a JSON file for tests and repeatable demos.
Every gate decision is written to the audit log, whichever mode is used.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .audit import AuditLog
    from .evidence import EvidenceRecord
    from .screening import EligibleComment
    from .validation import BlankValueIssue, LowSampleIssue, ReconciliationIssue

from .evidence import ANALYST_EXCERPT_SELECTION, AUTOMATIC_EXCERPT_SELECTION, readable_measure_label

_TRUTHY = {"y", "yes", "true", "1"}
_UNSPECIFIED_ANALYST = "(not provided)"
MAX_EXCERPTS = 2


def _default_excerpts(comment_ids: list[str]) -> list[str]:
    """The default excerpt choice: the first two linked IDs by ID (the IDs
    are already sorted when EvidenceRecord is built)."""
    return comment_ids[:MAX_EXCERPTS]


def _resolve_excerpts(requested: list[str] | None, linked_ids: list[str]) -> tuple[list[str], bool]:
    """Resolves an analyst's excerpt choice against the comments actually
    linked to this signal. `requested=None` means "the analyst made no
    choice" -> use the default (first two by ID), and the returned flag is
    True. An explicit list (scripted or interactive, even an empty one) is
    filtered to only the IDs that are genuinely linked (never invents an
    ID) and capped at MAX_EXCERPTS, preserving the order given; the flag is
    False, since a real choice was made even if it resolved to nothing."""
    if requested is None:
        return _default_excerpts(linked_ids), True
    linked_set = set(linked_ids)
    filtered = [cid for cid in dict.fromkeys(requested) if cid in linked_set]
    return filtered[:MAX_EXCERPTS], False


def _excerpt_selection_label(was_automatic: bool) -> str:
    return AUTOMATIC_EXCERPT_SELECTION if was_automatic else ANALYST_EXCERPT_SELECTION


class Gates:
    def __init__(
        self,
        answers_path: str | Path | None = None,
        audit: "AuditLog | None" = None,
        answers: dict | None = None,
    ):
        """Scripted mode is driven either by a JSON file (`answers_path`,
        the CLI's `--answers`) or by an in-memory dict (`answers`, used by
        the web dashboard to auto-resolve the pre-review gates with a
        fixed default policy without writing a temp file). Passing both is
        not meaningful; `answers_path` wins if both are given."""
        self.audit = audit
        self.scripted = answers_path is not None or answers is not None
        self.answers: dict = {}
        self.analyst_name: str = ""
        if answers_path is not None:
            self.answers = json.loads(Path(answers_path).read_text(encoding="utf-8"))
        elif answers is not None:
            self.answers = answers

    def _log(self, gate: str, **fields) -> None:
        if self.audit is not None:
            self.audit.log_event("gate", gate=gate, **fields)

    def _prompt(self, text: str) -> str:
        return input(text).strip()

    # --- 1. Scope gate --------------------------------------------------

    def confirm_scope(self, context) -> bool:
        print(f"Course: {context.course_code} — {context.course_title}")
        print(f"Period: {context.current_period} (compared with {context.previous_period})")
        for _key, filename, present in context.files_found:
            print(f"  {filename}: {'found' if present else 'MISSING'}")
        if self.scripted:
            name = str(self.answers.get("analyst_name", "")).strip()
            self.analyst_name = name or _UNSPECIFIED_ANALYST
            raw = self.answers.get("scope_confirm", False)
            decision = raw is True or str(raw).strip().lower() in _TRUTHY
        else:
            name = self._prompt("Your name (recorded for the audit trail and brief): ").strip()
            self.analyst_name = name or _UNSPECIFIED_ANALYST
            answer = self._prompt("Confirm scope and proceed? [y/N]: ")
            decision = answer.lower() in _TRUTHY
        self._log("scope_confirm", analyst=self.analyst_name, decision=decision)
        return decision

    # --- Validation pauses ------------------------------------------------

    def resolve_blank_value(self, issue: "BlankValueIssue") -> str:
        if self.scripted:
            table = self.answers.get("blank_value", {})
            decision = table.get(issue.issue_key, table.get("default", "abort"))
        else:
            print(f"PAUSE: {issue.file} is missing a value for '{issue.field}' in segment {issue.segment}.")
            print("The value will never be estimated, even if it looks derivable.")
            answer = self._prompt("Exclude this segment or abort the run? [exclude/abort]: ")
            decision = answer.lower() or "abort"
        decision = decision if decision in ("exclude", "abort") else "abort"
        self._log("blank_value", issue_key=issue.issue_key, segment=issue.segment, decision=decision)
        return decision

    def resolve_reconciliation(self, issue: "ReconciliationIssue") -> str:
        if self.scripted:
            table = self.answers.get("reconciliation", {})
            decision = table.get(issue.issue_key, table.get("default", "abort"))
        else:
            print(
                f"PAUSE: reconciliation mismatch for {issue.segment}: "
                f"enrolments={issue.enrolments} vs completions+in_progress+withdrawals={issue.sum_of_status_fields}."
            )
            answer = self._prompt("Exclude this segment or abort the run? [exclude/abort]: ")
            decision = answer.lower() or "abort"
        decision = decision if decision in ("exclude", "abort") else "abort"
        self._log("reconciliation", issue_key=issue.issue_key, segment=issue.segment, decision=decision)
        return decision

    def resolve_low_sample(self, issue: "LowSampleIssue") -> str:
        if self.scripted:
            table = self.answers.get("low_sample", {})
            decision = table.get(issue.issue_key, table.get("default", "exclude"))
        else:
            print(
                f"PAUSE: {issue.segment} has n_respondents={issue.n_respondents}, below the minimum sample size."
            )
            answer = self._prompt("Proceed with caution or exclude this row? [proceed/exclude]: ")
            decision = answer.lower() or "exclude"
        decision = decision if decision in ("proceed", "exclude") else "exclude"
        self._log("low_sample", issue_key=issue.issue_key, segment=issue.segment, decision=decision)
        return decision

    # --- Investigation pause ----------------------------------------------

    def ask_analyst(self, question: str) -> str:
        if self.scripted:
            table = self.answers.get("ask_analyst", {})
            answer = table.get(question, table.get("default", "No further information available; use only the recorded evidence."))
        else:
            answer = self._prompt(f"[Analyst question from investigation]\n{question}\nYour answer: ")
        self._log("ask_analyst", question=question, answer=answer)
        return answer

    # --- 7. Findings review gate --------------------------------------------

    def _print_linked_comments(self, label: str, comment_ids: list[str], comments_by_id: dict[str, "EligibleComment"]) -> None:
        if not comment_ids:
            print(f"  {label}: none")
            return
        print(f"  {label}:")
        for comment_id in comment_ids:
            comment = comments_by_id.get(comment_id)
            # comments_by_id is built only from eligible (non-quarantined)
            # comments (see main.py) — quarantined text can never reach here.
            text = comment.text if comment is not None else "(comment text unavailable)"
            print(f"    {comment_id}: {text}")

    def _choose_excerpts_interactively(self, label: str, comment_ids: list[str]) -> tuple[list[str], bool]:
        if not comment_ids:
            return [], True
        default = _default_excerpts(comment_ids)
        answer = self._prompt(
            f"Which up to {MAX_EXCERPTS} {label} comment(s) should appear as excerpts in the brief "
            f"(comma-separated IDs, default {', '.join(default)}): "
        ).strip()
        if not answer:
            return _resolve_excerpts(None, comment_ids)
        requested = [c.strip() for c in answer.split(",") if c.strip()]
        return _resolve_excerpts(requested, comment_ids)

    def review_findings(
        self,
        records: list["EvidenceRecord"],
        comments_by_id: dict[str, "EligibleComment"] | None = None,
    ) -> list["EvidenceRecord"]:
        comments_by_id = comments_by_id or {}
        table = self.answers.get("findings_review", {}) if self.scripted else {}
        for record in records:
            if self.scripted:
                entry = table.get(record.finding_id, table.get("default", "accept"))
                if isinstance(entry, dict):
                    decision = entry.get("decision", "accept")
                    note = entry.get("note", "")
                    excerpts_entry = entry.get("excerpts", {}) or {}
                else:
                    decision = entry
                    note = ""
                    excerpts_entry = {}
                aligned_excerpts, aligned_auto = _resolve_excerpts(excerpts_entry.get("aligned"), record.aligned_ids)
                countervailing_excerpts, countervailing_auto = _resolve_excerpts(
                    excerpts_entry.get("countervailing"), record.countervailing_ids
                )
            else:
                label = readable_measure_label(record.measure, record.segment)
                print(f"--- {record.finding_id}: {label} — {record.evidence_status} ---")
                print(f"  {record.calculation}; {record.comparison_rule}")
                self._print_linked_comments("Aligned", record.aligned_ids, comments_by_id)
                self._print_linked_comments("Countervailing", record.countervailing_ids, comments_by_id)
                answer = self._prompt("accept / edit / reject: ").lower()
                decision = answer if answer in ("accept", "edit", "reject") else "accept"
                note = self._prompt("Note: ") if decision == "edit" else ""
                aligned_excerpts, aligned_auto = self._choose_excerpts_interactively("aligned", record.aligned_ids)
                countervailing_excerpts, countervailing_auto = self._choose_excerpts_interactively(
                    "countervailing", record.countervailing_ids
                )
            decision = decision if decision in ("accept", "edit", "reject") else "accept"
            record.reviewer = self.analyst_name or _UNSPECIFIED_ANALYST
            record.reviewer_status = decision
            record.reviewer_note = note
            record.aligned_excerpt_ids = aligned_excerpts
            record.countervailing_excerpt_ids = countervailing_excerpts
            record.aligned_excerpt_selection = _excerpt_selection_label(aligned_auto)
            record.countervailing_excerpt_selection = _excerpt_selection_label(countervailing_auto)
            self._log(
                "findings_review",
                finding_id=record.finding_id,
                analyst=record.reviewer,
                decision=decision,
                note=note,
                aligned_excerpt_ids=aligned_excerpts,
                aligned_excerpt_selection=record.aligned_excerpt_selection,
                countervailing_excerpt_ids=countervailing_excerpts,
                countervailing_excerpt_selection=record.countervailing_excerpt_selection,
            )
        return records

    # --- 9. Final approval gate --------------------------------------------

    def final_approval(self, brief_path) -> str:
        if self.scripted:
            decision = str(self.answers.get("final_approval", "return")).lower()
        else:
            print(f"Draft brief written to: {brief_path}")
            answer = self._prompt("approve / return: ").lower()
            decision = answer or "return"
        decision = decision if decision in ("approve", "return") else "return"
        self._log("final_approval", decision=decision, brief_path=str(brief_path))
        return decision
