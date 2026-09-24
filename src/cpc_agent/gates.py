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
    from .validation import BlankValueIssue, LowSampleIssue, ReconciliationIssue

_TRUTHY = {"y", "yes", "true", "1"}


class Gates:
    def __init__(self, answers_path: str | Path | None = None, audit: "AuditLog | None" = None):
        self.audit = audit
        self.scripted = answers_path is not None
        self.answers: dict = {}
        if self.scripted:
            self.answers = json.loads(Path(answers_path).read_text(encoding="utf-8"))

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
            raw = self.answers.get("scope_confirm", False)
            decision = raw is True or str(raw).strip().lower() in _TRUTHY
        else:
            answer = self._prompt("Confirm scope and proceed? [y/N]: ")
            decision = answer.lower() in _TRUTHY
        self._log("scope_confirm", decision=decision)
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

    def review_findings(self, records: list["EvidenceRecord"]) -> list["EvidenceRecord"]:
        table = self.answers.get("findings_review", {}) if self.scripted else {}
        for record in records:
            if self.scripted:
                entry = table.get(record.finding_id, table.get("default", "accept"))
                if isinstance(entry, dict):
                    decision = entry.get("decision", "accept")
                    note = entry.get("note", "")
                else:
                    decision = entry
                    note = ""
            else:
                print(f"--- {record.finding_id}: {record.measure} ({record.segment}) — {record.evidence_status} ---")
                print(f"  {record.calculation}; {record.comparison_rule}")
                print(f"  Aligned: {record.aligned_ids} | Countervailing: {record.countervailing_ids}")
                answer = self._prompt("accept / edit / reject: ").lower()
                decision = answer if answer in ("accept", "edit", "reject") else "accept"
                note = self._prompt("Note: ") if decision == "edit" else ""
            decision = decision if decision in ("accept", "edit", "reject") else "accept"
            record.reviewer_status = decision
            record.reviewer_note = note
            self._log("findings_review", finding_id=record.finding_id, decision=decision, note=note)
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
