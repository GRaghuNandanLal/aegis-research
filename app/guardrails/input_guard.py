"""Screens user input for prompt injection, policy violations, and obvious abuse.

Strategy:
  1. Hard limits (length, control chars) -- cheap and deterministic.
  2. Heuristic pattern match for known injection phrases.
  3. PII redaction so downstream agents never see raw secrets.

We deliberately keep this fast and local so it can't be bypassed by LLM
hallucinations, and so the service degrades safely when the LLM is unavailable.
"""
from __future__ import annotations

import re
from typing import List

from ..models import GuardrailVerdict
from .pii import redact_pii

# Patterns sourced from public prompt-injection corpora + internal red-teaming.
INJECTION_PATTERNS = [
    r"ignore (all|the|previous|above|prior) (instructions|rules|prompts)",
    r"disregard (all|previous|above|prior) (instructions|rules|prompts)",
    r"forget (everything|all previous)",
    r"you are now (?:a|an|the) (?:dan|jailbroken|unrestricted)",
    r"system prompt",
    r"reveal (?:the|your) (?:system|hidden) prompt",
    r"print (?:the|your) (?:system|hidden) prompt",
    r"act as (?:a|an) (?:unrestricted|uncensored|evil)",
    r"\bdo anything now\b",
    r"</?\s*(system|assistant|tool)\s*>",  # fake role tags
]

DISALLOWED_TOPIC_PATTERNS = [
    r"\b(?:build|make|synthes(?:ize|is)) (?:a )?(?:bomb|explosive|bioweapon|chemical weapon)\b",
    r"\bcsam\b|\bchild (?:sexual|porn)",
    r"\bhow to (?:hack|compromise) (?:into )?[A-Za-z0-9._-]+\b",
    r"\bcredit card (?:dump|skimmer)\b",
]

CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _match_any(patterns: List[str], text: str) -> List[str]:
    hits = []
    for p in patterns:
        if re.search(p, text, flags=re.IGNORECASE):
            hits.append(p)
    return hits


def screen_input(text: str, *, max_chars: int) -> GuardrailVerdict:
    reasons: List[str] = []

    if not text or not text.strip():
        return GuardrailVerdict(allowed=False, reasons=["empty_input"], sanitized_text="")

    if len(text) > max_chars:
        reasons.append(f"input_too_long:{len(text)}>{max_chars}")

    cleaned = CONTROL_CHARS.sub(" ", text).strip()

    disallowed = _match_any(DISALLOWED_TOPIC_PATTERNS, cleaned)
    if disallowed:
        reasons.append("policy_violation")
        return GuardrailVerdict(
            allowed=False,
            reasons=reasons + [f"pattern:{p}" for p in disallowed],
            sanitized_text="",
        )

    injection_hits = _match_any(INJECTION_PATTERNS, cleaned)
    if injection_hits:
        # We don't block outright -- we neutralize by clearly tagging the segment
        # so the model treats it as untrusted user data, and we record the
        # reason for observability.
        reasons.append("prompt_injection_suspected")

    sanitized, n_redact = redact_pii(cleaned)
    if n_redact:
        reasons.append(f"pii_redacted:{n_redact}")

    if len(sanitized) > max_chars:
        sanitized = sanitized[:max_chars]
        reasons.append("input_truncated")

    return GuardrailVerdict(
        allowed=True,
        reasons=reasons,
        redactions=n_redact,
        sanitized_text=sanitized,
    )
