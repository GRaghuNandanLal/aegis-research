# Aegis Research — Multi-Agent System Design Report

**Use case:** an interactive *research & insight report generator* that turns
a one-line topic into a cited markdown brief by coordinating five specialized
agents behind a hardened guardrail layer.

**Author:** Raghu Nandan Lal &nbsp;·&nbsp; **Assignment:** Wipro Junior FDE Pre-screening (Apr 2026)

---

## 1. Multi-Agent Architecture

The system is composed of **five specialized agents** coordinated by a
deterministic **orchestrator** that owns control flow. No agent talks to
another directly; all communication is through a single typed `SessionState`
object, which keeps contracts explicit and makes the system easy to test.

| Agent          | Responsibility                                           | Can call tools? | LLM? |
|----------------|----------------------------------------------------------|-----------------|------|
| **Security**   | Screen inputs (policy, prompt injection, PII, length); re-screen outputs for leaks & PII | no | no (rules first) |
| **Planner**    | Decompose the topic into 3–5 independent sub-questions as strict JSON | no | yes |
| **Researcher** | For each sub-question, call `web_search`, then summarize with citations | `web_search` only | yes |
| **Critic**     | Review findings; emit a verdict and follow-up questions | no | yes |
| **Writer**     | Synthesize the final markdown report from *vetted* findings only | no | yes |

**Communication & control flow** (state-machine, not agent-autonomy):

```
  user ─▶ guard_in ─▶ plan ─▶ research ×N (parallel fan-out)
                              │
                              ▼
                            critique ─── acceptable? ─┐
                              ▲                       │ yes
                              │ no (≤ max_loops)      ▼
                              └─── follow-ups ─┐    write ─▶ guard_out ─▶ user
                                               │
                                               └── research ×M
```

Agents execute **sequentially at the pipeline level** but the Researcher
**fans out in parallel** (thread pool) across sub-questions to hide I/O
latency. The Critic can trigger a bounded loop (≤ 2 by default); the
orchestrator — not the LLM — decides when to stop.

**Why this shape?** It keeps each agent tiny (one system prompt, one
responsibility), places all hazardous decisions (tool calls, retries,
termination) in code we fully control, and leaves the LLM responsible only for
language tasks where it excels.

---

## 2. Security, Safety & Guardrails

Guardrails are implemented as a **defense-in-depth stack**, with deterministic
checks running first so the system degrades safely when the LLM is unavailable.

1. **Input screening** (`app/guardrails/input_guard.py`)
   - Hard limits: length, control characters stripped.
   - **Policy allowlist**: weapons/CSAM/illegal-hacking patterns → hard block;
     request never reaches the LLM or tools.
   - **Prompt-injection detection**: 10+ patterns from public corpora
     (`ignore previous instructions`, fake `<system>` tags, "DAN" personas).
     Matches are **neutralized, not just blocked**: the input is still wrapped
     in `<USER_TOPIC>…</USER_TOPIC>` delimiters and labelled untrusted in
     every system prompt.
   - **PII redaction** (regex + Luhn for credit cards) for emails, phones,
     SSNs, credit cards, IPs, and common API-key prefixes *before* the text
     reaches any LLM. Redactions are counted and logged.

2. **LLM guardrails**
   - Every system prompt freezes the agent's role ("You are the PLANNER /
     RESEARCHER / CRITIC / WRITER") and explicitly marks user/tool content as
     untrusted data.
   - Structured output via `response_format={"type":"json_object"}` with a
     regex-based fallback — the orchestrator never trusts freeform prose for
     control decisions.
   - Per-call token limits; total budget enforced by the orchestrator.

3. **Tool guardrails**
   - Exactly **one** tool is exposed (`web_search`). No code exec, no file I/O,
     no shell. The Researcher can *only* call this tool.
   - Optional domain allowlist (`ALLOW_TOOL_DOMAINS`).
   - Per-request quota (`MAX_TOOL_CALLS`) enforced by the orchestrator, not
     the agent.
   - Graceful fallback chain (Tavily → DuckDuckGo → deterministic mock) so a
     misbehaving upstream can never hang the pipeline.

4. **Output screening** (`app/guardrails/output_guard.py`)
   - Re-runs PII redaction on the Writer's markdown.
   - Flags attempts to leak the system prompt or emit fake role tags.

5. **Data handling & logging**
   - Only request IDs (random 12-char hex) are logged by default; raw inputs
     never go to stdout.
   - `.env` is gitignored; secrets are loaded via `pydantic-settings`.
   - Dockerfile runs as non-root.

6. **Unintended action prevention**
   - Orchestrator owns all state transitions; agents are pure functions of
     `SessionState`. The LLM cannot decide "what happens next".
   - Bounded loops, bounded fan-out, wall-clock timeout.
   - Failures are caught per-sub-question and logged; the pipeline still
     produces a partial report rather than throwing.

---

## 3. Implementation Approach

- **Language / runtime:** Python 3.11 (tested on 3.9 as well).
- **Server:** FastAPI + Uvicorn, with **Server-Sent Events** so the UI sees
  every agent event in real time (planner started, researcher found N
  citations, critic rejected with reasons, etc.).
- **LLM client:** official `openai` SDK pointed at any OpenAI-compatible
  endpoint (OpenAI, Azure OpenAI, Groq, Together, local vLLM). Retries via
  `tenacity` with exponential back-off; structured JSON mode with a
  regex-extraction fallback.
- **No agent framework**: the orchestrator is ~130 lines of hand-rolled state
  machine. This was a deliberate trade-off — I wanted to demonstrate the
  mechanics (parallel fan-out, budgets, critic loops) rather than hide them
  behind LangGraph/CrewAI/AutoGen, and to keep the dependency surface small
  and auditable.
- **Agent lifecycle:** stateless classes, instantiated once at app boot,
  invoked per-request with the request's `SessionState`. No shared mutable
  state between requests; workers use a per-request thread pool that is
  destroyed at the end of the request.
- **Error handling:** every LLM call wrapped in tenacity retries; every
  Researcher fan-out task wrapped in try/except so one failed sub-question
  can't kill the whole report; the orchestrator itself wraps the whole run
  and converts any exception into a graceful `status=error` session.
- **Testing:** 18 unit tests across guardrails and orchestrator flows, with a
  stubbed LLM so tests run offline in <1 s. Tests assert the critical
  invariants: blocked inputs never hit the LLM, PII never reaches the
  planner, budgets cap research, and the critic can trigger a bounded loop.
- **Deployment:** Dockerized (non-root, `uvicorn`), with deploy notes for
  Google Cloud Run, Render, and Fly.io in `README.md`.

---

## 4. Use of AI / LLMs and Collaboration

LLMs are used **only for language tasks** — planning, reading, critiquing,
writing — and never for control flow or policy decisions. Concretely:

- **Planner** (structured-JSON call): decomposes the topic.
- **Researcher** (structured-JSON call, one per sub-question): reads tool
  output and produces a cited summary.
- **Critic** (structured-JSON call): reviews the full set of findings.
- **Writer** (freeform markdown call): composes the final report from the
  vetted findings *only* — never from raw snippets.

**Collaboration pattern:** plan → parallel research → centralized critique →
optional re-research → synthesis. The Critic is the one agent that can change
what the others do, and even that is bounded by `MAX_CRITIQUE_LOOPS` — the
orchestrator, not the Critic, decides termination.

**Autonomy vs. control trade-off.** The design deliberately sits near the
*control* end of the spectrum: the LLMs choose *what to write* but never
*what to do next* and never *what tool to call*. This sacrifices some
flexibility (e.g., the Planner can't dynamically invent new tools) in
exchange for auditability, predictable cost, and safety — which is the right
trade for a demo-quality system that could be hardened into production
without redesigning the control flow.

---

## 5. Evaluation & Known Limitations

- Guardrail regexes cover the common cases but are not a replacement for
  managed services like Presidio/Comprehend in production.
- The Critic is itself an LLM; a prompt-injected tool result could in theory
  nudge it to accept sub-par findings. Mitigations: tool output is delimited
  and tagged untrusted; the Critic has no tool access; only the Writer's
  *vetted* output is returned to the user and it, too, is re-screened.
- Web search quality with DuckDuckGo HTML is best-effort; Tavily is the
  recommended production backend.
- No long-term memory or multi-user auth — out of scope for a 3-day demo.

## 6. How to Run / Deploy

See `README.md`. Local: `pip install -r requirements.txt && uvicorn app.main:app`.
Deploy to Cloud Run / Render / Fly.io with the supplied `Dockerfile`; set
`OPENAI_API_KEY` (and optionally `TAVILY_API_KEY`) as environment variables.
