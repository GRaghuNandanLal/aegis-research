"""Screens model output before it is returned to the user.

We do two things:
  * Redact any PII that slipped in from tool results.
  * Flag if the model appears to be leaking its system prompt or obeying an
    injected instruction (e.g., emitting fake tool-call tags).
"""
from __future__ import annotations

import re
from typing import List

from ..models import GuardrailVerdict
from .pii import redact_pii

LEAK_PATTERNS = [
    r"you are (?:the )?(?:aegis |security )?(?:planner|researcher|critic|writer|security) agent",
    r"system prompt:",
    r"<\s*/?\s*(system|assistant|tool)\s*>",
]


def screen_output(text: str) -> GuardrailVerdict:
    reasons: List[str] = []
    if not text:
        return GuardrailVerdict(allowed=False, reasons=["empty_output"], sanitized_text="")

    lowered = text.lower()
    for p in LEAK_PATTERNS:
        if re.search(p, lowered):
            reasons.append(f"possible_leak:{p}")

    sanitized, n = redact_pii(text)
    if n:
        reasons.append(f"pii_redacted:{n}")

    return GuardrailVerdict(
        allowed=True,
        reasons=reasons,
        redactions=n,
        sanitized_text=sanitized,
    )
