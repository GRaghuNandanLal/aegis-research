"""Security agent.

Unlike the other agents, this one is *deterministic first*: it runs the
rule-based guardrails before (and after) the LLM pipeline. We deliberately do
NOT trust an LLM to be the sole gatekeeper. The LLM is only used as an
additional signal when available, never as the sole decision-maker.
"""
from __future__ import annotations

from ..config import get_settings
from ..guardrails import screen_input, screen_output
from ..models import EventKind, GuardrailVerdict, SessionState


class SecurityAgent:
    name = "security"

    def screen_input(self, state: SessionState) -> GuardrailVerdict:
        s = get_settings()
        state.log(EventKind.AGENT_START, "screening input", agent=self.name)
        v = screen_input(state.user_input_raw, max_chars=s.max_input_chars)
        state.input_guard = v
        state.user_input_clean = v.sanitized_text
        state.log(
            EventKind.AGENT_END,
            "input " + ("allowed" if v.allowed else "blocked"),
            agent=self.name,
            data={"reasons": v.reasons, "redactions": v.redactions},
        )
        return v

    def screen_output(self, state: SessionState, text: str) -> GuardrailVerdict:
        state.log(EventKind.AGENT_START, "screening output", agent=self.name)
        v = screen_output(text)
        state.output_guard = v
        state.log(
            EventKind.AGENT_END,
            "output " + ("cleaned" if v.redactions else "clean"),
            agent=self.name,
            data={"reasons": v.reasons, "redactions": v.redactions},
        )
        return v
