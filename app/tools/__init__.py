"""External tools the agents are allowed to call.

Tools live behind a narrow interface so the Security agent + orchestrator can
enforce allowlists, quotas, and timeouts uniformly.
"""
from .search import web_search

__all__ = ["web_search"]
