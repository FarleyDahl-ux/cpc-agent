"""Deterministic evidence-status assignment and evidence record construction.

Evidence status is decided entirely in code (data_dictionary.txt "Evidence
status"), never by the LLM — the LLM (investigate.py) only proposes aligned/
countervailing links; this module applies the precedence rule to whatever
links survive.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from . import config
from .metrics import Signal

REVIEW_REQUIRED = "Review required"
MIXED = "Mixed"
SUFFICIENT = "Sufficient"
LIMITED = "Limited"
NO_QUALITATIVE_EVIDENCE = "No qualitative evidence"

AUTOMATIC_EXCERPT_SELECTION = "automatic selection (first two by ID)"
ANALYST_EXCERPT_SELECTION = "analyst selection"
NO_LINKED_COMMENTS_SELECTION = "none"


def assign_evidence_status(
    aligned_ids: list[str],
    countervailing_ids: list[str],
    review_required_reason: str | None = None,
) -> str:
    """Applies the evidence-status precedence order, most conditions first:
    Review required -> Mixed -> Sufficient -> Limited -> No qualitative
    evidence. IDs are deduplicated (unique respondents)."""
    if review_required_reason:
        return REVIEW_REQUIRED
    aligned = set(aligned_ids)
    countervailing = set(countervailing_ids)
    if aligned and countervailing:
        return MIXED
    if not countervailing and len(aligned) >= config.EVIDENCE_SUFFICIENT_MIN_ALIGNED:
        return SUFFICIENT
    if not countervailing and len(aligned) >= config.EVIDENCE_LIMITED_MIN_ALIGNED:
        return LIMITED
    return NO_QUALITATIVE_EVIDENCE


_DEFAULT_LIMITATIONS = {
    REVIEW_REQUIRED: "",  # supplied by the caller via review_required_reason
    MIXED: (
        "Aligned and countervailing feedback were both found; feedback is reported "
        "experience and does not establish cause."
    ),
    SUFFICIENT: (
        "Based on a small number of unique comments; not a representative sample of "
        "all learners."
    ),
    LIMITED: (
        "Based on only one or two eligible comments; not a representative sample of "
        "all learners."
    ),
    NO_QUALITATIVE_EVIDENCE: "No eligible comments could reasonably be linked to this signal.",
}


@dataclass
class EvidenceRecord:
    finding_id: str
    measure: str
    segment: str
    calculation: str
    comparison_rule: str
    source_file: str
    period: str
    n: float
    value: float = 0.0
    details: dict = field(default_factory=dict)
    aligned_ids: list[str] = field(default_factory=list)
    countervailing_ids: list[str] = field(default_factory=list)
    aligned_excerpt_ids: list[str] = field(default_factory=list)
    countervailing_excerpt_ids: list[str] = field(default_factory=list)
    aligned_excerpt_selection: str = AUTOMATIC_EXCERPT_SELECTION
    countervailing_excerpt_selection: str = AUTOMATIC_EXCERPT_SELECTION
    ambiguous_links: dict[str, str] = field(default_factory=dict)  # comment_id -> a short note explaining the uncertainty
    themes: list[str] = field(default_factory=list)
    evidence_status: str = ""
    limitation: str = ""
    reviewer: str = ""
    reviewer_status: str = "Pending"
    reviewer_note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def humanize_measure(measure: str) -> str:
    """Turns a code-name measure (e.g. 'teacher_support') into readable
    words ('teacher support'), for display only. The stored `measure` field
    on EvidenceRecord/Signal/evidence_records.json always keeps the code
    name — this is never written back into any JSON output."""
    return measure.replace("_", " ").strip()


def readable_measure_label(measure: str, segment: str) -> str:
    """A human-readable label for a signal's measure+segment, for display
    in the brief and at the findings review gate only (e.g. "Teacher
    support satisfaction (Online)"). JSON records keep the raw code names."""
    if measure == "unit_withdrawal_rate":
        base = "Unit withdrawal rate"
    elif measure == "segment_withdrawal_rate":
        base = "Withdrawal rate"
    else:
        base = f"{humanize_measure(measure).capitalize()} satisfaction"
    return f"{base} ({segment})"


def build_evidence_record(
    signal: Signal,
    aligned_ids: list[str],
    countervailing_ids: list[str],
    themes: list[str],
    review_required_reason: str | None = None,
    extra_limitation: str = "",
    ambiguous_links: dict[str, str] | None = None,
) -> EvidenceRecord:
    status = assign_evidence_status(aligned_ids, countervailing_ids, review_required_reason)
    limitation = review_required_reason if status == REVIEW_REQUIRED else _DEFAULT_LIMITATIONS[status]
    if extra_limitation:
        limitation = f"{limitation} {extra_limitation}".strip()
    return EvidenceRecord(
        finding_id=signal.id,
        measure=signal.measure,
        segment=signal.segment,
        calculation=signal.calculation,
        comparison_rule=signal.comparison_rule,
        source_file=signal.source_file,
        period=signal.period,
        n=signal.n,
        value=signal.value,
        details=dict(signal.details),
        aligned_ids=sorted(set(aligned_ids)),
        countervailing_ids=sorted(set(countervailing_ids)),
        ambiguous_links=dict(ambiguous_links or {}),
        themes=sorted(set(themes)),
        evidence_status=status,
        limitation=limitation,
        reviewer_status="Pending",
        reviewer_note="",
    )
