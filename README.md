# Aegis Research

A **multi-agent research & insight report generator** with first-class
security guardrails. Five specialized agents (Security, Planner, Researcher,
Critic, Writer) collaborate behind a deterministic orchestrator to turn a
one-line topic into a cited markdown brief, streamed live to the browser.

Built for the **Wipro Junior FDE pre-screening assignment** (April 2026).
The full design write-up is in [`REPORT.md`](REPORT.md); the architecture
diagrams are in [`ARCHITECTURE.md`](ARCHITECTURE.md).

**Live demo:** https://aegis-research.onrender.com &nbsp;·&nbsp;
**Report:** [`REPORT.md`](REPORT.md) &nbsp;·&nbsp;
**Architecture:** [`ARCHITECTURE.md`](ARCHITECTURE.md) &nbsp;·&nbsp;
**Sample prompts:** [`docs/sample_prompts.md`](docs/sample_prompts.md)

![status](https://img.shields.io/badge/tests-22%20passing-brightgreen)
![python](https://img.shields.io/badge/python-3.9%2B-blue)
![deploy](https://img.shields.io/badge/deploy-Render-46b3b3)

---

## What it demonstrates

- **Multi-agent collaboration** — Planner → Researcher (parallel) → Critic
  (bounded loop) → Writer, with a Security agent wrapping the whole thing.
- **Safe LLM use** — prompt-injection neutralization, PII redaction before
  and after the pipeline, tool allowlisting, delimiter-wrapped untrusted
  data, output re-screening, per-request budgets.
- **System thinking** — deterministic state-machine orchestrator (no
  LangGraph/CrewAI), typed state, thread-pool fan-out, tenacity retries,
  graceful degradation when the LLM or search is unavailable.
- **Full stack** — FastAPI backend with SSE streaming, a zero-dependency
  HTML/JS UI, Dockerfile for one-command deploy, 18 unit tests.

---

## Quick start (local)

```bash
git clone <this-repo>
cd wipro
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and set OPENAI_API_KEY; optionally TAVILY_API_KEY

uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Open [http://localhost:8080](http://localhost:8080).

### Offline / demo mode

If you don't have API keys but want to see the UI + guardrails in action:

```bash
ENABLE_SEARCH_MOCK=true uvicorn app.main:app
```

Inputs that trip the policy or prompt-injection filters will block
*before* any LLM call is made — you can see this even without an API key.

---

## Run the tests

```bash
ENABLE_SEARCH_MOCK=true OPENAI_API_KEY=test-key python -m pytest -v
# 18 passed in <1s — no network, no real LLM calls
```

---

## Deploy

### Docker

```bash
docker build -t aegis-research .
docker run --rm -p 8080:8080 \
  -e OPENAI_API_KEY=$OPENAI_API_KEY \
  -e TAVILY_API_KEY=$TAVILY_API_KEY \
  aegis-research
```

### Google Cloud Run (one-liner)

```bash
gcloud run deploy aegis-research \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars OPENAI_API_KEY=$OPENAI_API_KEY,TAVILY_API_KEY=$TAVILY_API_KEY
```

### Render / Fly.io

Both auto-detect the `Dockerfile`. Set `OPENAI_API_KEY` (and optionally
`TAVILY_API_KEY`) in the dashboard; no other config needed. Set `PORT` to
whatever the platform provides.

---

## Project layout

```
wipro/
├── app/
│   ├── main.py              FastAPI app (static UI + REST + SSE)
│   ├── orchestrator.py      State-machine orchestrator
│   ├── llm.py               OpenAI-compatible client (JSON mode + retries)
│   ├── config.py            pydantic-settings with all limits
│   ├── models.py            typed SessionState, events, findings, etc.
│   ├── agents/
│   │   ├── security.py      screens input/output (rules-first)
│   │   ├── planner.py       decomposes topic into sub-questions
│   │   ├── researcher.py    web_search + cited summary
│   │   ├── critic.py        verdict + follow-ups
│   │   └── writer.py        final markdown report
│   ├── guardrails/
│   │   ├── input_guard.py   length, policy, injection, PII
│   │   ├── output_guard.py  leak check, PII redaction
│   │   └── pii.py           regex + Luhn
│   ├── tools/
│   │   └── search.py        Tavily → DuckDuckGo → mock
│   └── static/index.html    zero-dep UI
├── tests/                   18 tests (guardrails + orchestrator)
├── docs/sample_prompts.md   try-these prompts for the demo
├── Dockerfile
├── REPORT.md                1-2 page design write-up
├── ARCHITECTURE.md          mermaid diagrams
└── requirements.txt
```

---

## Configuration

All knobs are environment variables (see `.env.example`). The most common:

| Variable             | Default        | Purpose                                   |
|----------------------|----------------|-------------------------------------------|
| `OPENAI_API_KEY`     | *(required)*   | Any OpenAI-compatible endpoint            |
| `OPENAI_BASE_URL`    | —              | Point at Azure / Groq / Together / vLLM   |
| `OPENAI_MODEL`       | `gpt-4o-mini`  | Tested with 4o-mini; any chat model works |
| `TAVILY_API_KEY`     | —              | Premium search; DDG fallback otherwise    |
| `ENABLE_SEARCH_MOCK` | `false`        | Force mock search (offline demos)         |
| `MAX_SUBQUESTIONS`   | 5              | Planner cap                               |
| `MAX_TOOL_CALLS`     | 12             | Hard cap on search calls per request      |
| `MAX_CRITIQUE_LOOPS` | 2              | Critic-driven retry cap                   |
| `ALLOW_TOOL_DOMAINS` | `*`            | Comma-separated hostname allowlist        |

---

## API

- `POST /api/research` → `{request_id}`
- `GET  /api/stream/:request_id` — Server-Sent Events (agent trace + final)
- `GET  /api/session/:request_id` — full `SessionState` JSON
- `GET  /healthz` — health + config summary
- `GET  /api/config` — public subset of config for the UI

---

Video Link
- https://drive.google.com/file/d/1Ozdn0BBpN6kyeXZugDaRTJ3cXNDtWa_1/view?usp=sharing

---

## License

MIT for the reference implementation; see top of each file for attribution
notes on public prompt-injection patterns.
