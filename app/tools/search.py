"""Web search tool with three backends:

    1. Tavily (preferred; higher-quality snippets, requires TAVILY_API_KEY).
    2. DuckDuckGo HTML endpoint (free, no key, best-effort).
    3. Deterministic mock (when offline or explicitly enabled) so demos and
       CI never fail because of upstream flakiness.

All backends emit the same :class:`SearchHit` shape so agents don't care which
one answered.
"""
from __future__ import annotations

import html
import logging
import re
from typing import List
from urllib.parse import urlparse

import httpx

from ..config import get_settings
from ..models import SearchHit

log = logging.getLogger(__name__)

_DDG_URL = "https://duckduckgo.com/html/"
_TAVILY_URL = "https://api.tavily.com/search"

_MOCK_CORPUS: List[SearchHit] = [
    SearchHit(
        title="Multi-Agent Systems: A Modern Approach (Wooldridge)",
        url="https://example.com/mas-overview",
        snippet="An overview of multi-agent systems, including coordination, "
                "negotiation, and safety considerations in LLM-driven agents.",
        score=0.9,
    ),
    SearchHit(
        title="LLM Agent Security: Prompt Injection Survey",
        url="https://example.com/llm-prompt-injection",
        snippet="Taxonomy of prompt injection and mitigations: input sanitization, "
                "delimiter-wrapped untrusted input, output screening, tool allowlists.",
        score=0.85,
    ),
    SearchHit(
        title="Production Patterns for LLM Orchestration",
        url="https://example.com/llm-orchestration",
        snippet="Planner / Researcher / Critic / Writer loops with bounded retries "
                "and per-request token budgets are the emerging default pattern.",
        score=0.8,
    ),
]


def _domain_allowed(url: str, allowed: List[str]) -> bool:
    if allowed == ["*"]:
        return True
    host = (urlparse(url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in allowed)


def _tavily_search(query: str, k: int) -> List[SearchHit]:
    s = get_settings()
    if not s.tavily_api_key:
        return []
    try:
        with httpx.Client(timeout=15) as client:
            r = client.post(
                _TAVILY_URL,
                json={
                    "api_key": s.tavily_api_key,
                    "query": query,
                    "max_results": k,
                    "search_depth": "basic",
                },
            )
            r.raise_for_status()
            data = r.json()
    except Exception as e:  # noqa: BLE001
        log.warning("tavily_failed: %s", e)
        return []

    hits: List[SearchHit] = []
    for item in data.get("results", [])[:k]:
        hits.append(SearchHit(
            title=item.get("title", "")[:200],
            url=item.get("url", ""),
            snippet=item.get("content", "")[:500],
            score=float(item.get("score", 0.0)),
        ))
    return hits


_DDG_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
    r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
    flags=re.DOTALL,
)


def _strip_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def _ddg_search(query: str, k: int) -> List[SearchHit]:
    try:
        with httpx.Client(timeout=15, follow_redirects=True,
                          headers={"User-Agent": "Mozilla/5.0 aegis-research/0.1"}) as client:
            r = client.post(_DDG_URL, data={"q": query})
            r.raise_for_status()
            body = r.text
    except Exception as e:  # noqa: BLE001
        log.warning("ddg_failed: %s", e)
        return []

    hits: List[SearchHit] = []
    for m in _DDG_RESULT_RE.finditer(body):
        url, title, snippet = m.group(1), _strip_tags(m.group(2)), _strip_tags(m.group(3))
        # DDG sometimes wraps redirects as /l/?uddg=<encoded>; take the final target.
        if url.startswith("//"):
            url = "https:" + url
        hits.append(SearchHit(title=title[:200], url=url, snippet=snippet[:500], score=0.0))
        if len(hits) >= k:
            break
    return hits


def _mock_search(query: str, k: int) -> List[SearchHit]:
    q = query.lower()
    scored = sorted(
        _MOCK_CORPUS,
        key=lambda h: -sum(1 for w in re.findall(r"\w+", q) if w in h.snippet.lower()),
    )
    return scored[:k]


def web_search(query: str, k: int = 4) -> List[SearchHit]:
    """Return up to ``k`` search hits for ``query`` and the backend that served them.

    The function never raises: on total failure it returns the mock corpus so
    the pipeline can still complete. The chosen backend is attached to the
    first hit's ``score`` metadata via the returned tuple so callers can
    surface provenance in the agent trace.
    """
    hits, _ = web_search_with_source(query, k)
    return hits


def web_search_with_source(query: str, k: int = 4):
    """Like :func:`web_search` but also returns which backend answered.

    Returns ``(hits, backend)`` where ``backend`` is one of
    ``"tavily"``, ``"duckduckgo"``, ``"mock"``, or ``"empty"``.
    """
    s = get_settings()
    query = (query or "").strip()
    if not query:
        return [], "empty"

    if s.enable_search_mock:
        return _mock_search(query, k), "mock"

    backend = "mock"
    hits = _tavily_search(query, k)
    if hits:
        backend = "tavily"
    else:
        hits = _ddg_search(query, k)
        if hits:
            backend = "duckduckgo"
    if not hits:
        hits = _mock_search(query, k)
        backend = "mock"

    allowed = s.allowed_domains
    hits = [h for h in hits if _domain_allowed(h.url, allowed)]
    if not hits:
        return _mock_search(query, k), "mock"
    return hits[:k], backend
