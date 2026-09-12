"""Provider selection -- the single place that knows which backend is active."""

from __future__ import annotations

from functools import lru_cache

from jarvis.config import get_settings
from jarvis.llm.base import LLMProvider


@lru_cache(maxsize=1)
def get_provider() -> LLMProvider:
    """Return the configured LLM provider (currently always Gemini)."""
    from jarvis.llm.gemini import GeminiProvider

    settings = get_settings()
    return GeminiProvider(
        api_key=settings.gemini_api_key, model=settings.gemini_model
    )
