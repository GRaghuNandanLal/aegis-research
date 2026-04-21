"""FastAPI app: static UI + REST + Server-Sent-Events stream."""
from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Dict

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .models import EventKind, ResearchRequest, SessionState
from .orchestrator import Orchestrator

logging.basicConfig(
    level=get_settings().log_level,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
log = logging.getLogger("aegis")

app = FastAPI(title="Aegis Research", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


_orchestrator = Orchestrator()
_sessions: Dict[str, SessionState] = {}
_streams: Dict[str, "queue.Queue[str]"] = {}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/healthz")
def healthz() -> dict:
    s = get_settings()
    return {
        "ok": True,
        "llm_configured": s.has_llm,
        "model": s.openai_model if s.has_llm else None,
        "search_mock": s.enable_search_mock,
    }


@app.get("/api/config")
def config() -> dict:
    s = get_settings()
    return {
        "model": s.openai_model if s.has_llm else None,
        "llm_configured": s.has_llm,
        "max_input_chars": s.max_input_chars,
        "max_subquestions": s.max_subquestions,
        "max_critique_loops": s.max_critique_loops,
        "max_tool_calls": s.max_tool_calls,
    }


def _run_session(state: SessionState, q: "queue.Queue[str]") -> None:
    """Runs the orchestrator on a worker thread, relaying events to ``q``."""
    last_idx = 0

    def pump() -> None:
        nonlocal last_idx
        while last_idx < len(state.events):
            ev = state.events[last_idx]
            last_idx += 1
            q.put(json.dumps({
                "kind": ev.kind.value,
                "agent": ev.agent,
                "message": ev.message,
                "data": ev.data,
                "ts": ev.ts,
            }))

    done = threading.Event()

    def pumper() -> None:
        while not done.is_set():
            pump()
            time.sleep(0.1)
        pump()

    t = threading.Thread(target=pumper, daemon=True)
    t.start()
    try:
        _orchestrator.run(state)
    finally:
        done.set()
        t.join(timeout=1.0)
        # Send a terminal event so the client can close the SSE stream.
        q.put(json.dumps({
            "kind": "final",
            "agent": "orchestrator",
            "message": state.status,
            "data": {
                "status": state.status,
                "error": state.error,
                "report": state.report.model_dump() if state.report else None,
                "request_id": state.request_id,
            },
            "ts": time.time(),
        }))
        q.put("__END__")


@app.post("/api/research")
def research(req: ResearchRequest) -> dict:
    state = SessionState(user_input_raw=req.query)
    _sessions[state.request_id] = state
    q: "queue.Queue[str]" = queue.Queue()
    _streams[state.request_id] = q
    threading.Thread(target=_run_session, args=(state, q), daemon=True).start()
    return {"request_id": state.request_id}


@app.get("/api/stream/{request_id}")
async def stream(request_id: str) -> StreamingResponse:
    q = _streams.get(request_id)
    if q is None:
        raise HTTPException(404, "unknown request_id")

    async def gen():
        loop = asyncio.get_event_loop()
        while True:
            item = await loop.run_in_executor(None, q.get)
            if item == "__END__":
                break
            yield f"data: {item}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/session/{request_id}")
def get_session(request_id: str) -> dict:
    st = _sessions.get(request_id)
    if not st:
        raise HTTPException(404, "unknown request_id")
    return st.model_dump()


if __name__ == "__main__":
    import uvicorn

    s = get_settings()
    uvicorn.run("app.main:app", host=s.host, port=s.port, reload=False)
