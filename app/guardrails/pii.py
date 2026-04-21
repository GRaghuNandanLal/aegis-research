"""Lightweight regex-based PII detector & redactor.

This is intentionally conservative: the goal is to keep obvious PII out of LLM
prompts and server logs. Production deployments should layer on something like
Microsoft Presidio or AWS Comprehend.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Tuple

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+?\d{1,2}[\s\-\.])?\(?\d{3}\)?[\s\-\.]\d{3}[\s\-\.]\d{4}\b")
SSN_RE = re.compile(r"\b(?!000|666|9\d{2})\d{3}[- ]?(?!00)\d{2}[- ]?(?!0000)\d{4}\b")
CC_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
API_KEY_RE = re.compile(r"\b(?:sk|pk|rk|tvly|AIza|ghp|xoxb)[A-Za-z0-9_\-]{20,}\b")


def _luhn_ok(digits: str) -> bool:
    """Luhn check to reduce credit-card false positives."""
    s = 0
    alt = False
    for ch in reversed(digits):
        if not ch.isdigit():
            continue
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        s += d
        alt = not alt
    return s > 0 and s % 10 == 0


@dataclass
class PIIMatch:
    kind: str
    value: str
    start: int
    end: int


def find_pii(text: str) -> List[PIIMatch]:
    matches: List[PIIMatch] = []

    for m in API_KEY_RE.finditer(text):
        matches.append(PIIMatch("api_key", m.group(), m.start(), m.end()))
    for m in EMAIL_RE.finditer(text):
        matches.append(PIIMatch("email", m.group(), m.start(), m.end()))
    for m in SSN_RE.finditer(text):
        matches.append(PIIMatch("ssn", m.group(), m.start(), m.end()))
    for m in PHONE_RE.finditer(text):
        matches.append(PIIMatch("phone", m.group(), m.start(), m.end()))
    for m in CC_RE.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            matches.append(PIIMatch("credit_card", m.group(), m.start(), m.end()))
    for m in IP_RE.finditer(text):
        octs = [int(x) for x in m.group().split(".")]
        if all(0 <= o <= 255 for o in octs):
            matches.append(PIIMatch("ip", m.group(), m.start(), m.end()))

    # Deduplicate overlapping matches, preferring the earliest-longest match.
    matches.sort(key=lambda x: (x.start, -(x.end - x.start)))
    deduped: List[PIIMatch] = []
    last_end = -1
    for mm in matches:
        if mm.start >= last_end:
            deduped.append(mm)
            last_end = mm.end
    return deduped


def redact_pii(text: str) -> Tuple[str, int]:
    """Return (redacted_text, num_redactions)."""
    hits = find_pii(text)
    if not hits:
        return text, 0
    out = []
    cursor = 0
    for h in hits:
        out.append(text[cursor:h.start])
        out.append(f"[REDACTED_{h.kind.upper()}]")
        cursor = h.end
    out.append(text[cursor:])
    return "".join(out), len(hits)
