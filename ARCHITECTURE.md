# Architecture

## Component diagram

```mermaid
flowchart TB
    subgraph Client["Browser UI"]
      UI[index.html\nSSE consumer]
    end

    subgraph API["FastAPI service"]
      direction TB
      R[/POST /api/research/]
      S[/GET /api/stream/:id/ SSE]
      H[/GET /healthz/]
    end

    subgraph Orchestrator["Orchestrator (state machine)"]
      direction TB
      G1[guard_in]
      P[plan]
      RF[research ×N parallel]
      C[critique]
      W[write]
      G2[guard_out]
      G1 --> P --> RF --> C
      C -- acceptable --> W --> G2
      C -- not acceptable\n(&le; MAX_LOOPS) --> RF
    end

    subgraph Agents["Specialized agents"]
      SEC[Security Agent\n(rules-first)]
      PL[Planner Agent\nJSON]
      RS[Researcher Agent\nJSON + web_search]
      CR[Critic Agent\nJSON]
      WR[Writer Agent\nmarkdown]
    end

    subgraph External["External services"]
      LLM[(LLM\nOpenAI-compatible)]
      TAV[(Tavily)]
      DDG[(DuckDuckGo)]
      MOCK[(Mock corpus)]
    end

    UI --> R --> Orchestrator
    Orchestrator -. events .-> S --> UI

    G1 --> SEC
    P --> PL --> LLM
    RF --> RS --> LLM
    RS --> TAV
    RS --> DDG
    RS --> MOCK
    C --> CR --> LLM
    W --> WR --> LLM
    G2 --> SEC
```

## State object

All agents read/write a single `SessionState` (see `app/models.py`). This is
the *only* shared mutable surface; there is no message bus, no shared memory
between requests, no cross-request state. Each request gets its own state,
thread pool, and lifecycle.

## Guardrail layering

```mermaid
flowchart LR
  U[User input] --> L[Length check]
  L --> POL[Policy patterns]
  POL --> INJ[Injection patterns<br/>(neutralize, don't block)]
  INJ --> PII1[PII redaction]
  PII1 --> LLM[LLM agents]
  LLM --> OUT[Writer output]
  OUT --> LEAK[Prompt-leak check]
  LEAK --> PII2[PII redaction]
  PII2 --> USER[Return to user]
```

Layers run in **this order** so that the cheapest, most reliable (rule-based)
checks run first. If any earlier layer can make a safe decision, later layers
are skipped.

## Budgets

| Knob                  | Default | Purpose                                   |
|-----------------------|---------|-------------------------------------------|
| `MAX_INPUT_CHARS`     | 4000    | DoS protection, prompt-budget control     |
| `MAX_SUBQUESTIONS`    | 5       | Planner scope cap                         |
| `MAX_CRITIQUE_LOOPS`  | 2       | Prevents infinite self-critique           |
| `MAX_TOOL_CALLS`      | 12      | Hard cap on `web_search` calls            |
| `REQUEST_TIMEOUT_S`   | 90      | Wall-clock guard (soft, checked per node) |
