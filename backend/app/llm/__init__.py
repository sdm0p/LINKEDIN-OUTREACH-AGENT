"""Single place that decides which LLM provider is used.

Swapping to Ollama/Anthropic later = add a module under app/llm/ and
change this factory only.
"""

from functools import lru_cache

from ..config import settings
from .base import LLMError, LLMProvider
from .gemini import GeminiProvider


@lru_cache(maxsize=1)
def get_llm_provider() -> LLMProvider:
    if not settings.gemini_api_key:
        raise LLMError(
            "LLM provider not configured — set GEMINI_API_KEY in backend/.env "
            "and restart the backend."
        )
    return GeminiProvider(api_key=settings.gemini_api_key, model=settings.gemini_model)


def provider_info() -> dict:
    return {
        "provider": "gemini",
        "model": settings.gemini_model,
        "configured": bool(settings.gemini_api_key),
    }


__all__ = ["LLMError", "LLMProvider", "get_llm_provider", "provider_info"]
