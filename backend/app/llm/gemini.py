import asyncio
import json
from typing import Any

from google import genai
from google.genai import types

from .base import LLMError

# Free-tier transient failures (429 quota, 503 high-demand) are common;
# retry with backoff before giving up.
_RETRY_DELAYS = (2.0, 5.0, 10.0)
_TRANSIENT_MARKERS = ("429", "503", "500", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "overload")


def _strip_code_fences(text: str) -> str:
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2 and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return text


class GeminiProvider:
    """Free-tier Gemini/Gemma via the google-genai SDK. JSON mode is
    requested through response_mime_type (Gemini models only — Gemma
    rejects it); a fence-stripping fallback keeps parsing robust when the
    model wraps output anyway."""

    name = "gemini"

    def __init__(self, api_key: str, model: str) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model
        # Gemma models support system instructions but not JSON mode.
        self._supports_json_mode = not model.lower().startswith("gemma-")

    @property
    def model(self) -> str:
        return self._model

    async def generate_json(self, *, system: str, user: str) -> dict[str, Any]:
        # The loop only exits via break (success) or raise (non-transient
        # error, or transient error after the final attempt).
        for attempt in range(len(_RETRY_DELAYS) + 1):
            try:
                response = await self._client.aio.models.generate_content(
                    model=self._model,
                    contents=user,
                    config=types.GenerateContentConfig(
                        system_instruction=system,
                        response_mime_type=(
                            "application/json" if self._supports_json_mode else None
                        ),
                        temperature=0.2,
                    ),
                )
                break
            except Exception as exc:  # network, quota, auth -> uniform error
                transient = any(m in str(exc) for m in _TRANSIENT_MARKERS)
                if transient and attempt < len(_RETRY_DELAYS):
                    await asyncio.sleep(_RETRY_DELAYS[attempt])
                    continue
                raise LLMError(f"Gemini call failed: {exc}") from exc

        text = (response.text or "").strip()
        if not text:
            raise LLMError("Gemini returned an empty response")

        try:
            data = json.loads(_strip_code_fences(text))
        except json.JSONDecodeError as exc:
            snippet = text[:200].replace("\n", " ")
            raise LLMError(f"Gemini response was not valid JSON: {snippet}") from exc

        if not isinstance(data, dict):
            raise LLMError("Gemini response was not a JSON object")
        return data
