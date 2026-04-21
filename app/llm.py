"""Thin wrapper around the OpenAI-compatible Chat Completions API.

The wrapper enforces:
  * timeouts & retries (tenacity)
  * a stable JSON-extraction helper (so agents can request structured output)
  * a graceful offline fallback so the whole system remains demo-able without
    an API key (useful for CI and the first 30 seconds of a live demo).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    OpenAI,
    PermissionDeniedError,
    RateLimitError,
)
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from .config import get_settings

log = logging.getLogger(__name__)

_JSON_BLOCK = re.compile(r"\{[\s\S]*\}")

# Errors that will never recover by retrying. Surface them immediately so we
# don't burn time and quota hammering the upstream.
_NON_RETRYABLE = (
    AuthenticationError,
    PermissionDeniedError,
    BadRequestError,
    NotFoundError,
)


def _is_quota_error(exc: BaseException) -> bool:
    """A 429 with code='insufficient_quota' means billing/quota, not RPM.
    Retrying it just wastes calls and returns the same error.
    """
    if not isinstance(exc, RateLimitError):
        return False
    body = getattr(exc, "body", None) or {}
    if isinstance(body, dict):
        err = body.get("error") or {}
        if isinstance(err, dict) and err.get("code") == "insufficient_quota":
            return True
    msg = str(exc).lower()
    return "insufficient_quota" in msg or "exceeded your current quota" in msg


def _should_retry(exc: BaseException) -> bool:
    if isinstance(exc, _NON_RETRYABLE):
        return False
    if _is_quota_error(exc):
        return False
    if isinstance(exc, (RateLimitError, APIConnectionError, APITimeoutError)):
        return True
    return True


class LLMError(RuntimeError):
    """Raised by the LLM client with a human-friendly, agent-safe message."""

    def __init__(self, message: str, *, kind: str = "unknown") -> None:
        super().__init__(message)
        self.kind = kind


class LLMClient:
    def __init__(self) -> None:
        s = get_settings()
        self._settings = s
        self._client: Optional[OpenAI] = None
        if s.openai_api_key:
            kwargs: Dict[str, Any] = {"api_key": s.openai_api_key, "timeout": 60.0}
            if s.openai_base_url:
                kwargs["base_url"] = s.openai_base_url
            self._client = OpenAI(**kwargs)

    @property
    def enabled(self) -> bool:
        return self._client is not None

    @retry(
        reraise=True,
        retry=retry_if_exception(_should_retry),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=4.0),
    )
    def _call_raw(self, messages: List[Dict[str, str]], *, temperature: float,
                  max_tokens: int,
                  response_format: Optional[Dict[str, str]] = None) -> str:
        assert self._client is not None
        kwargs: Dict[str, Any] = {
            "model": self._settings.openai_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            kwargs["response_format"] = response_format
        resp = self._client.chat.completions.create(**kwargs)
        return (resp.choices[0].message.content or "").strip()

    def _call(self, messages: List[Dict[str, str]], *, temperature: float,
              max_tokens: int, response_format: Optional[Dict[str, str]] = None) -> str:
        """Call the LLM, translating SDK errors into clean ``LLMError``s."""
        try:
            return self._call_raw(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )
        except AuthenticationError as e:
            log.warning("llm_auth_error model=%s", self._settings.openai_model)
            raise LLMError(
                "OpenAI rejected the API key (HTTP 401). "
                "Check OPENAI_API_KEY in your environment / .env file.",
                kind="auth",
            ) from e
        except PermissionDeniedError as e:
            raise LLMError(
                "OpenAI denied access (HTTP 403). The key may be valid but lack "
                "access to the requested model or organization.",
                kind="permission",
            ) from e
        except NotFoundError as e:
            raise LLMError(
                f"Model '{self._settings.openai_model}' not found at the configured "
                "endpoint. Set OPENAI_MODEL to a model your key has access to.",
                kind="model_not_found",
            ) from e
        except RateLimitError as e:
            if _is_quota_error(e):
                raise LLMError(
                    "OpenAI returned 'insufficient_quota' (HTTP 429). This is a "
                    "billing/quota problem, not a per-minute rate limit -- add a "
                    "payment method or credit at "
                    "https://platform.openai.com/settings/organization/billing , "
                    "or point OPENAI_BASE_URL at another OpenAI-compatible "
                    "provider (e.g. Groq).",
                    kind="quota",
                ) from e
            raise LLMError(
                "OpenAI rate-limited the request after retries. Try again shortly "
                "or lower the request rate.",
                kind="rate_limit",
            ) from e
        except (APIConnectionError, APITimeoutError) as e:
            raise LLMError(
                "Network error talking to the LLM endpoint. Check connectivity / "
                "OPENAI_BASE_URL.",
                kind="network",
            ) from e
        except BadRequestError as e:
            raise LLMError(f"LLM rejected the request: {e}", kind="bad_request") from e

    def complete(self, system: str, user: str, *, temperature: float = 0.2,
                 max_tokens: int = 800) -> str:
        if not self.enabled:
            raise LLMError("LLM not configured (set OPENAI_API_KEY)", kind="not_configured")
        return self._call(
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def complete_json(self, system: str, user: str, *, temperature: float = 0.1,
                      max_tokens: int = 800) -> Any:
        """Request JSON output and parse it robustly.

        Uses the native ``response_format={"type":"json_object"}`` when
        supported, and falls back to greedy JSON extraction.
        """
        if not self.enabled:
            raise LLMError("LLM not configured (set OPENAI_API_KEY)", kind="not_configured")

        sys_msg = system + "\n\nRespond ONLY with a single JSON object. No prose."
        messages = [
            {"role": "system", "content": sys_msg},
            {"role": "user", "content": user},
        ]
        try:
            raw = self._call(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"},
            )
        except LLMError as e:
            # Auth / permission / model-not-found will not improve by retrying
            # without response_format -- propagate immediately.
            if e.kind in {"auth", "permission", "model_not_found", "not_configured"}:
                raise
            raw = self._call(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )

        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            m = _JSON_BLOCK.search(raw)
            if not m:
                raise LLMError(
                    f"model did not return JSON: {raw[:200]}",
                    kind="bad_output",
                )
            return json.loads(m.group(0))
