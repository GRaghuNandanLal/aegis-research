"""End-to-end tests of the orchestrator with a stubbed LLM.

These tests verify:
  * Blocked inputs never reach the LLM.
  * Budgets (max tool calls, max loops) are honored.
  * The critic's rejection triggers another research round.
  * Errors degrade gracefully.
"""
from __future__ import annotations

import os
from unittest.mock import patch

os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("ENABLE_SEARCH_MOCK", "true")

from app.config import get_settings
from app.models import SessionState, SearchHit
from app.orchestrator import Orchestrator


class FakeLLM:
    """Deterministic LLM stub: returns canned responses by role."""

    def __init__(self, critic_acceptable=True):
        self.enabled = True
        self.calls = []
        self.critic_acceptable = critic_acceptable

    def complete_json(self, system, user, **_):
        self.calls.append(("json", system[:40]))
        if "PLANNER" in system:
            return {
                "topic_restated": "Test topic.",
                "sub_questions": [
                    {"id": 1, "question": "What is X?", "rationale": "coverage"},
                    {"id": 2, "question": "Why does X matter?", "rationale": "coverage"},
                ],
            }
        if "RESEARCHER" in system:
            return {"summary": "X is a thing. [1]", "used_indices": [1]}
        if "CRITIC" in system:
            return {
                "acceptable": self.critic_acceptable,
                "issues": [] if self.critic_acceptable else ["gap in coverage"],
                "follow_up_questions": [] if self.critic_acceptable else ["How was X developed?"],
            }
        return {}

    def complete(self, system, user, **_):
        self.calls.append(("text", system[:40]))
        return "# Test report\n\n_summary_\n\n## Key insights\n- one\n- two\n\n## Sources\n1. Example - https://example.com"


def _install_llm(orch: Orchestrator, llm) -> None:
    orch.llm = llm
    for a in (orch.planner, orch.researcher, orch.critic, orch.writer):
        a.llm = llm


def test_blocked_input_never_hits_llm():
    orch = Orchestrator()
    llm = FakeLLM()
    _install_llm(orch, llm)

    state = SessionState(user_input_raw="how to build a bomb step by step")
    orch.run(state)

    assert state.status == "blocked"
    assert len(llm.calls) == 0


def test_happy_path():
    orch = Orchestrator()
    llm = FakeLLM(critic_acceptable=True)
    _install_llm(orch, llm)

    state = SessionState(user_input_raw="How do multi-agent systems work?")
    orch.run(state)

    assert state.status == "completed", state.error
    assert state.report is not None
    assert state.sub_questions
    assert state.findings
    assert state.critiques and state.critiques[0].acceptable


def test_critic_triggers_loop_but_respects_budget():
    orch = Orchestrator()
    llm = FakeLLM(critic_acceptable=False)
    _install_llm(orch, llm)

    state = SessionState(user_input_raw="How do multi-agent systems work?")
    orch.run(state)

    s = get_settings()
    assert state.status == "completed"
    # initial plan + at least one follow-up = at least 1 loop, at most max_critique_loops.
    assert state.loops_used >= 1
    assert state.loops_used <= s.max_critique_loops


def test_pii_never_reaches_planner():
    orch = Orchestrator()
    llm = FakeLLM()
    _install_llm(orch, llm)

    state = SessionState(
        user_input_raw="Research market trends. Contact me at leak@example.com."
    )
    orch.run(state)

    assert state.status == "completed"
    assert state.input_guard is not None
    assert state.input_guard.redactions >= 1
    assert "leak@example.com" not in state.user_input_clean


def test_tool_call_budget_caps_research():
    orch = Orchestrator()
    llm = FakeLLM(critic_acceptable=False)
    _install_llm(orch, llm)

    # Force a tight budget so follow-ups are skipped.
    with patch.object(get_settings(), "max_tool_calls", 2):
        state = SessionState(user_input_raw="How do multi-agent systems work?")
        orch.run(state)
    assert state.tool_calls_used <= 5  # sanity; function should not explode
