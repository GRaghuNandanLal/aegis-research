"""State-machine orchestrator.

Flow:

    guard_in -> plan -> research (parallel fan-out) -> critique
                                     ^                      |
                                     |                      v
                                     +-- [loop if issues] --+
                                                           |
                                                     write -> guard_out -> final

Design goals:
  * Deterministic control flow. The LLM decides *what* to write, never *where*
    control goes next. This makes the system auditable and bounds blast radius.
  * Budgets everywhere: max sub-questions, max tool calls, max critique loops,
    max wall-clock. Any breach yields a graceful partial result, never a hang.
  * Observability: every state transition becomes an ``Event`` that is streamed
    to the UI via SSE.

We roll our own state machine rather than depending on LangGraph/CrewAI so the
control flow is auditable in ~120 lines and the dependency surface is minimal.
"""
from __future__ import annotations

import concurrent.futures as cf
import logging
import time
from typing import Iterable

from .agents import (
    CriticAgent,
    PlannerAgent,
    ResearcherAgent,
    SecurityAgent,
    WriterAgent,
)
from .config import get_settings
from .llm import LLMClient, LLMError
from .models import EventKind, FinalReport, SessionState, SubQuestion

log = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self) -> None:
        self.llm = LLMClient()
        self.security = SecurityAgent()
        self.planner = PlannerAgent(self.llm)
        self.researcher = ResearcherAgent(self.llm)
        self.critic = CriticAgent(self.llm)
        self.writer = WriterAgent(self.llm)

    # --- public API ---------------------------------------------------------

    def run(self, state: SessionState) -> SessionState:
        s = get_settings()
        t0 = time.time()
        state.status = "running"

        try:
            if not self._guard_in(state):
                state.status = "blocked"
                return state
            if not self.llm.enabled:
                state.error = "LLM not configured. Set OPENAI_API_KEY."
                state.status = "error"
                state.log(EventKind.ERROR, state.error, agent="orchestrator")
                return state

            self._plan(state)
            self._research_all(state.sub_questions, state)

            for loop in range(s.max_critique_loops + 1):
                verdict = self.critic.run(state)
                if verdict.acceptable or loop == s.max_critique_loops:
                    break
                if state.tool_calls_used >= s.max_tool_calls:
                    state.log(EventKind.WARN, "tool-call budget reached, skipping extra loop",
                              agent="orchestrator")
                    break
                if (time.time() - t0) > s.request_timeout_s * 0.7:
                    state.log(EventKind.WARN, "time budget getting tight, skipping extra loop",
                              agent="orchestrator")
                    break

                extras = self._materialize_follow_ups(
                    state, verdict.follow_up_questions,
                )
                if not extras:
                    break
                state.loops_used += 1
                self._research_all(extras, state)

            self._write(state)
            self._guard_out(state)

            state.status = "completed"
            # NOTE: use INFO (not FINAL) so the UI doesn't mistake this
            # per-run summary for the terminal event that carries the report
            # payload (that one is emitted by the API layer in main.py).
            state.log(EventKind.INFO, "done", agent="orchestrator",
                      data={"elapsed_s": round(time.time() - t0, 2),
                            "tool_calls": state.tool_calls_used,
                            "loops": state.loops_used})
        except LLMError as e:
            state.error = str(e)
            state.status = "error"
            state.log(EventKind.ERROR, state.error, agent="orchestrator",
                      data={"kind": getattr(e, "kind", "unknown")})
        except Exception as e:  # noqa: BLE001
            log.exception("orchestrator_failed")
            state.error = f"internal error: {type(e).__name__}: {e}"
            state.status = "error"
            state.log(EventKind.ERROR, state.error, agent="orchestrator")

        return state

    # --- nodes --------------------------------------------------------------

    def _guard_in(self, state: SessionState) -> bool:
        v = self.security.screen_input(state)
        if not v.allowed:
            state.log(EventKind.BLOCKED, "input blocked by security agent",
                      agent="orchestrator", data={"reasons": v.reasons})
            return False
        return True

    def _plan(self, state: SessionState) -> None:
        self.planner.run(state)

    def _research_all(self, questions: Iterable[SubQuestion], state: SessionState) -> None:
        s = get_settings()
        remaining = s.max_tool_calls - state.tool_calls_used
        qs = [q for q in questions][: max(0, remaining)]
        if not qs:
            return
        # Fan out with a small thread pool; the underlying HTTP+LLM calls are
        # I/O bound so threads are fine and keep the orchestrator framework-free.
        with cf.ThreadPoolExecutor(max_workers=min(4, len(qs))) as pool:
            futs = {pool.submit(self.researcher.run, state, q): q for q in qs}
            for fut in cf.as_completed(futs):
                q = futs[fut]
                try:
                    fut.result()
                except Exception as e:  # noqa: BLE001
                    state.log(EventKind.WARN, f"research failed for SQ{q.id}: {e}",
                              agent="researcher", data={"sub_question_id": q.id})

    def _materialize_follow_ups(self, state: SessionState,
                                questions: Iterable[str]) -> list[SubQuestion]:
        s = get_settings()
        existing = {q.question.lower() for q in state.sub_questions}
        out: list[SubQuestion] = []
        next_id = (max((q.id for q in state.sub_questions), default=0)) + 1
        for q in questions:
            q = (q or "").strip()
            if not q or q.lower() in existing:
                continue
            sq = SubQuestion(id=next_id, question=q[:300], rationale="critic follow-up")
            out.append(sq)
            state.sub_questions.append(sq)
            next_id += 1
            if len(out) >= max(1, s.max_subquestions // 2):
                break
        return out

    def _write(self, state: SessionState) -> FinalReport:
        return self.writer.run(state)

    def _guard_out(self, state: SessionState) -> None:
        if not state.report:
            return
        v = self.security.screen_output(state, state.report.markdown)
        if v.redactions or "possible_leak" in " ".join(v.reasons):
            state.report.markdown = v.sanitized_text
