"""Centralized configuration loaded from environment / .env.

All tunable knobs live here so that ops can reason about limits at a glance.
"""
from __future__ import annotations

from functools import lru_cache
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM ---
    openai_api_key: Optional[str] = Field(default=None, alias="OPENAI_API_KEY")
    openai_base_url: Optional[str] = Field(default=None, alias="OPENAI_BASE_URL")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")

    # --- Search ---
    tavily_api_key: Optional[str] = Field(default=None, alias="TAVILY_API_KEY")
    enable_search_mock: bool = Field(default=False, alias="ENABLE_SEARCH_MOCK")

    # --- Guardrails / limits ---
    max_input_chars: int = Field(default=4000, alias="MAX_INPUT_CHARS")
    max_subquestions: int = Field(default=5, alias="MAX_SUBQUESTIONS")
    max_critique_loops: int = Field(default=2, alias="MAX_CRITIQUE_LOOPS")
    max_tool_calls: int = Field(default=12, alias="MAX_TOOL_CALLS")
    request_timeout_s: int = Field(default=90, alias="REQUEST_TIMEOUT_S")
    allow_tool_domains: str = Field(default="*", alias="ALLOW_TOOL_DOMAINS")

    # --- Server ---
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8080, alias="PORT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @property
    def allowed_domains(self) -> List[str]:
        raw = (self.allow_tool_domains or "").strip()
        if not raw or raw == "*":
            return ["*"]
        return [d.strip().lower() for d in raw.split(",") if d.strip()]

    @property
    def has_llm(self) -> bool:
        return bool(self.openai_api_key)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
