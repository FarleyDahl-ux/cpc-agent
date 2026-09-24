"""Comment screening: runs in plain code, before any LLM call.

Quarantines possible identifying information and possible prompt-injection
attempts. Quarantined comment text is never returned from screen_comments —
only the eligible comments (for the LLM/evidence stage) and an exception
list carrying id + reason (for the exception log) are produced.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

TITLE_NAME_PATTERN = re.compile(
    r"\b(?:Mr|Mrs|Ms|Miss|Dr)\.?\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b"
)
EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")
PHONE_PATTERN = re.compile(r"(?:\+?\d[\d\-\s()]{7,}\d)")

INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior)\s+instructions", re.IGNORECASE),
    re.compile(r"disregard\s+(?:all\s+)?(?:previous|prior|the)?\s*instructions?", re.IGNORECASE),
    re.compile(r"system\s+prompt", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\b", re.IGNORECASE),
]

IDENTIFYING_INFORMATION = "identifying_information"
POSSIBLE_INJECTION = "possible_injection"


@dataclass
class EligibleComment:
    comment_id: str
    reporting_period: str
    delivery_region: str
    delivery_mode: str
    prompt: str
    text: str


@dataclass
class QuarantinedComment:
    comment_id: str
    reasons: list[str] = field(default_factory=list)

    @property
    def reason(self) -> str:
        return "; ".join(self.reasons)


@dataclass
class ScreeningResult:
    eligible: list[EligibleComment] = field(default_factory=list)
    quarantined: list[QuarantinedComment] = field(default_factory=list)


_QUARANTINE_SUMMARIES = {
    IDENTIFYING_INFORMATION: "A comment was held back from review because it appeared to identify a person.",
    POSSIBLE_INJECTION: (
        "A comment was held back from review because it looked like an attempt to change how "
        "this tool behaves, rather than genuine feedback."
    ),
}
_QUARANTINE_SUMMARY_FALLBACK = "A comment was held back from review."


def quarantine_summary(reasons: list[str]) -> str:
    """A plain-language sentence for the brief's exceptions section — never
    the comment_id or the comment text, just why something was held back.
    Technical reason codes stay in exception_log.json."""
    phrases: list[str] = []
    for reason in reasons:
        phrase = _QUARANTINE_SUMMARIES.get(reason, _QUARANTINE_SUMMARY_FALLBACK)
        if phrase not in phrases:
            phrases.append(phrase)
    return " ".join(phrases) if phrases else _QUARANTINE_SUMMARY_FALLBACK


def _contains_identifying_information(text: str) -> bool:
    return bool(
        TITLE_NAME_PATTERN.search(text)
        or EMAIL_PATTERN.search(text)
        or PHONE_PATTERN.search(text)
    )


def _contains_possible_injection(text: str) -> bool:
    return any(pattern.search(text) for pattern in INJECTION_PATTERNS)


def screen_comments(rows: list[dict[str, str]]) -> ScreeningResult:
    result = ScreeningResult()
    for row in rows:
        comment_id = row.get("comment_id", "")
        text = row.get("comment", "")
        reasons: list[str] = []
        if _contains_identifying_information(text):
            reasons.append(IDENTIFYING_INFORMATION)
        if _contains_possible_injection(text):
            reasons.append(POSSIBLE_INJECTION)
        if reasons:
            result.quarantined.append(QuarantinedComment(comment_id=comment_id, reasons=reasons))
            continue
        result.eligible.append(
            EligibleComment(
                comment_id=comment_id,
                reporting_period=row.get("reporting_period", ""),
                delivery_region=row.get("delivery_region", ""),
                delivery_mode=row.get("delivery_mode", ""),
                prompt=row.get("prompt", ""),
                text=text,
            )
        )
    return result
