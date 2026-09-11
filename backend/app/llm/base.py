"""Provider-agnostic LLM interface.

The plan requires that swapping providers (Gemini -> Ollama/Anthropic later)
is a one-file change: implement this protocol in a new module under
app/llm/ and point the factory in app/llm/__init__.py at it.
"""

from typing import Any, Protocol


class LLMError(RuntimeError):
    """Raised when the provider is unconfigured, unreachable, or returns
    an unusable response."""


class LLMProvider(Protocol):
    name: str

    async def generate_json(self, *, system: str, user: str) -> dict[str, Any]:
        """Run one prompt and return the model's response parsed as a
        JSON object. Implementations must raise LLMError on any failure."""
        ...
