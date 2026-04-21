"""LLM client error mapping.

Verifies that:
  * Auth errors are NOT retried (they never recover).
  * Auth errors surface as a clean LLMError with kind='auth' so the UI can
    display a helpful message instead of a stack trace.
  * Network errors ARE retried.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("OPENAI_API_KEY", "test-key")

import httpx
from openai import APIConnectionError, AuthenticationError, RateLimitError

from app.llm import LLMClient, LLMError


def _make_auth_error() -> AuthenticationError:
    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    resp = httpx.Response(401, request=req, json={
        "error": {"message": "Incorrect API key", "type": "invalid_request_error",
                  "code": "invalid_api_key"}
    })
    return AuthenticationError("Incorrect API key", response=resp, body=None)


def _make_network_error() -> APIConnectionError:
    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    return APIConnectionError(request=req)


def test_auth_error_is_not_retried_and_is_translated():
    client = LLMClient()
    # Pretend we have an SDK client; replace its create() method.
    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = _make_auth_error()
    client._client = fake_client

    try:
        client.complete("sys", "user", max_tokens=10)
    except LLMError as e:
        assert e.kind == "auth", f"expected kind=auth, got {e.kind}"
        assert "401" in str(e) or "API key" in str(e)
    else:
        raise AssertionError("expected LLMError")

    # Auth errors must NOT trigger tenacity retries; expect exactly 1 call.
    assert fake_client.chat.completions.create.call_count == 1


def _make_quota_error() -> RateLimitError:
    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    body = {"error": {"message": "You exceeded your current quota",
                      "type": "insufficient_quota", "code": "insufficient_quota"}}
    resp = httpx.Response(429, request=req, json=body)
    return RateLimitError("insufficient_quota", response=resp, body=body)


def _make_rate_limit_error() -> RateLimitError:
    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    body = {"error": {"message": "Rate limit reached", "type": "rate_limit_error",
                      "code": "rate_limit_exceeded"}}
    resp = httpx.Response(429, request=req, json=body)
    return RateLimitError("rate_limit_exceeded", response=resp, body=body)


def test_quota_error_is_not_retried_and_says_billing():
    client = LLMClient()
    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = _make_quota_error()
    client._client = fake_client

    try:
        client.complete("sys", "user", max_tokens=10)
    except LLMError as e:
        assert e.kind == "quota", f"expected kind=quota, got {e.kind}"
        assert "billing" in str(e).lower() or "quota" in str(e).lower()
    else:
        raise AssertionError("expected LLMError")

    # Quota errors must NOT trigger tenacity retries -- the next call would
    # return the same error and just burn an extra API request.
    assert fake_client.chat.completions.create.call_count == 1


def test_true_rate_limit_is_retried():
    client = LLMClient()
    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = _make_rate_limit_error()
    client._client = fake_client

    try:
        client.complete("sys", "user", max_tokens=10)
    except LLMError as e:
        assert e.kind == "rate_limit"
    else:
        raise AssertionError("expected LLMError")

    assert fake_client.chat.completions.create.call_count == 3


def test_network_error_is_retried_then_translated():
    client = LLMClient()
    fake_client = MagicMock()
    fake_client.chat.completions.create.side_effect = _make_network_error()
    client._client = fake_client

    try:
        client.complete("sys", "user", max_tokens=10)
    except LLMError as e:
        assert e.kind == "network"
    else:
        raise AssertionError("expected LLMError")

    # Network errors should be retried up to 3 times by tenacity.
    assert fake_client.chat.completions.create.call_count == 3
