"""Typed state shared between agents.

The orchestrator passes a single ``SessionState`` through the graph; each node
reads what it needs and appends to ``events`` + its own field. This keeps the
contract between agents explicit and testable.
"""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class EventKind(str, Enum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    AGENT_START = "agent_start"
    AGENT_END = "agent_end"
    TOOL_CALL = "tool_call"
    TOKEN = "token"
    BLOCKED = "blocked"
    FINAL = "final"


class Event(BaseModel):
    ts: float = Field(default_factory=time.time)
    kind: EventKind
    agent: Optional[str] = None
    message: str = ""
    data: Optional[dict] = None


class SearchHit(BaseModel):
    title: str
    url: str
    snippet: str
    score: float = 0.0


class SubQuestion(BaseModel):
    id: int
    question: str
    rationale: str = ""


class ResearchFinding(BaseModel):
    sub_question_id: int
    summary: str
    citations: List[SearchHit] = Field(default_factory=list)


class CritiqueVerdict(BaseModel):
    acceptable: bool
    issues: List[str] = Field(default_factory=list)
    follow_up_questions: List[str] = Field(default_factory=list)


class GuardrailVerdict(BaseModel):
    allowed: bool
    reasons: List[str] = Field(default_factory=list)
    redactions: int = 0
    sanitized_text: str = ""


class FinalReport(BaseModel):
    title: str
    markdown: str
    citations: List[SearchHit] = Field(default_factory=list)


class SessionState(BaseModel):
    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    user_input_raw: str
    user_input_clean: str = ""
    topic: str = ""

    input_guard: Optional[GuardrailVerdict] = None
    output_guard: Optional[GuardrailVerdict] = None

    sub_questions: List[SubQuestion] = Field(default_factory=list)
    findings: List[ResearchFinding] = Field(default_factory=list)
    critiques: List[CritiqueVerdict] = Field(default_factory=list)
    report: Optional[FinalReport] = None

    tool_calls_used: int = 0
    loops_used: int = 0
    events: List[Event] = Field(default_factory=list)
    status: Literal["pending", "running", "completed", "blocked", "error"] = "pending"
    error: Optional[str] = None

    def log(self, kind: EventKind, message: str, agent: Optional[str] = None,
            data: Optional[dict] = None) -> Event:
        ev = Event(kind=kind, agent=agent, message=message, data=data)
        self.events.append(ev)
        return ev


class ResearchRequest(BaseModel):
    query: str = Field(min_length=3)
