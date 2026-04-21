"""Critic agent: reviews the collected findings and decides whether to loop.

The critic cannot call tools or change findings -- it can only emit a verdict.
The orchestrator is responsible for acting on the verdict (loop vs write).
"""
from __future__ import annotations

from typing import List

from ..llm import LLMClient
from ..models import CritiqueVerdict, EventKind, ResearchFinding, SessionState

SYSTEM_PROMPT = """You are the CRITIC agent.
Review a set of findings against the research topic and return a verdict.

Focus on:
- Coverage gaps (topics the findings don't answer).
- Unsupported or contradictory claims across findings.
- Weak or missing citations.

Be strict but terse. Do NOT rewrite the findings.
"""

USER_TEMPLATE = """Topic: {topic}

Findings:
{findings_block}

Return JSON:
{{
  "acceptable": true|false,
  "issues": ["..."],
  "follow_up_questions": ["..."]
}}
"""


def _render_findings(findings: List[ResearchFinding]) -> str:
    if not findings:
        return "(none)"
    chunks = []
    for f in findings:
        urls = ", ".join(c.url for c in f.citations) or "(no citations)"
        chunks.append(f"- SQ{f.sub_question_id}: {f.summary}\n    sources: {urls}")
    return "\n".join(chunks)


class CriticAgent:
    name = "critic"

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def run(self, state: SessionState) -> CritiqueVerdict:
        state.log(EventKind.AGENT_START, "critiquing", agent=self.name)
        data = self.llm.complete_json(
            SYSTEM_PROMPT,
            USER_TEMPLATE.format(
                topic=state.topic or state.user_input_clean,
                findings_block=_render_findings(state.findings),
            ),
            temperature=0.1,
            max_tokens=400,
        )
        verdict = CritiqueVerdict(
            acceptable=bool(data.get("acceptable", True)),
            issues=[str(x)[:240] for x in (data.get("issues") or [])][:6],
            follow_up_questions=[str(x)[:240] for x in (data.get("follow_up_questions") or [])][:3],
        )
        state.critiques.append(verdict)
        state.log(
            EventKind.AGENT_END,
            "accepted" if verdict.acceptable else f"rejected ({len(verdict.issues)} issues)",
            agent=self.name,
            data=verdict.model_dump(),
        )
        return verdict
