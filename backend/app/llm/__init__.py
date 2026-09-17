"""Single place that decides which LLM provider is used.

Swapping to Ollama/Anthropic later = add a module under app/llm/ and
change this factory only.
"""

from ..config import settings
from .base import LLMError, LLMProvider
from .gemini import GeminiProvider


def get_llm_provider() -> LLMProvider:
    """Build a provider from the effective key (runtime-set via the
    Settings API wins over .env). Intentionally NOT cached: keys can
    change at runtime, and the SDK client is cheap to construct."""
    key = settings.effective_gemini_api_key()
    if not key:
        raise LLMError(
            "LLM provider not configured — add your Gemini API key on the "
            "Settings page (or set GEMINI_API_KEY in backend/.env)."
        )
    return GeminiProvider(api_key=key, model=settings.gemini_model)


def provider_info() -> dict:
    return {
        "provider": "gemini",
        "model": settings.gemini_model,
        "configured": settings.key_source() is not None,
        "source": settings.key_source(),
    }


__all__ = ["LLMError", "LLMProvider", "get_llm_provider", "provider_info"]
