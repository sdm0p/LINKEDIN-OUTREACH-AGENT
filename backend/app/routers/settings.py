"""Settings page endpoints: LLM provider, LinkedIn MCP prerequisites,
draft cap, and data-retention info.

Full cookie health checking arrives with the live validation pass; the
status shown here reflects cheap local prerequisites so the UI is honest
about what is known without spawning a browser.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ..config import get_target_countries, set_target_countries, settings
from ..llm import LLMError, provider_info
from ..pipeline.location import selectable_target_countries
from ..search.base import get_linkedin_source, reset_source_registry
from ..search.linkedin_mcp import prerequisites
from ..services import keyword_service, queue_service, resume_service
from ..db import get_resume_cache

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("")
async def get_settings() -> dict:
    retention = await keyword_service.retention_stats()
    resume_state = await resume_service.get_resume_state()
    all_drafts = await queue_service.list_queue()
    drafts_new = [d for d in all_drafts if d["status"] == "new"]
    drafts_pending = [d for d in all_drafts if d["status"] == "pending"]
    prereq = prerequisites()

    if not prereq["docker_installed"]:
        status, detail = "unavailable", "Docker is not installed or not on PATH."
    elif not prereq["session_dir_present"]:
        status, detail = "not_configured", (
            "No LinkedIn session found — run the one-time login "
            "(docker run ... --login --login-viewer, port 6080), then use "
            "the manual health check on the Run page."
        )
    else:
        status, detail = "unknown", (
            "Session volume exists. Run the manual health check to verify "
            "the session is live."
        )

    return {
        "llm": provider_info(),
        "location_targets": {
            "countries": get_target_countries(),
            "available": selectable_target_countries(),
        },
        "linkedin": {
            "status": status,
            "detail": detail,
            "docker_installed": prereq["docker_installed"],
            "session_dir_present": prereq["session_dir_present"],
        },
        "search_source": {
            "name": settings.effective_search_source(),
            "available_sources": ["mcp", "playwright"],
        },
        "drafts": {
            "max_per_day": settings.max_drafts_per_day,
            "sent_today": 0,
        },
        "retention": {
            **retention,
            "resume_cached": resume_state.has_resume,
            "drafts_total": len(all_drafts),
            "drafts_new": len(drafts_new),
            "drafts_pending": len(drafts_pending),
        },
    }


class _KeyPayload(BaseModel):
    api_key: str


@router.post("/llm/key")
async def set_llm_key(payload: _KeyPayload) -> dict:
    """Set the Gemini API key from the Settings page. Live-verifies it with
    a one-token call before persisting; stores it in the data dir (the
    Docker volume in packaged deployments) so it survives restarts. The
    key is never returned by any endpoint."""
    key = payload.api_key.strip()
    if not key:
        return JSONResponse(status_code=400, content={"detail": "API key is required."})
    if not key.startswith("AIza") or len(key) < 30:
        return JSONResponse(status_code=400, content={"detail": "That does not look like a Gemini API key (should start with 'AIza'). Get one at aistudio.google.com/apikey."})

    # Verify before persisting — one real call, so a wrong key never gets saved.
    from ..llm.gemini import GeminiProvider

    probe = GeminiProvider(api_key=key, model=settings.gemini_model)
    try:
        await probe.generate_json(
            system="Reply with JSON only.", user='Return {"ok": true} and nothing else.'
        )
    except LLMError as exc:
        return JSONResponse(status_code=400, content={"detail": f"Key rejected: {str(exc)[:200]}"})

    settings.set_api_key_runtime(key)
    return {"ok": True, "verified": True}


class _TargetsPayload(BaseModel):
    countries: list[str]


@router.put("/location-targets")
async def put_location_targets(payload: _TargetsPayload):
    """Set the countries runs should find hiring posts in. Every value must
    be a selectable country (no free text, no 'Remote' — a bare remote post
    is not provably inside or outside any country). Empty list clears the
    geo filter entirely."""
    available = selectable_target_countries()
    cleaned: list[str] = []
    for value in payload.countries:
        name = value.strip()
        if not name:
            continue
        if name not in available:
            return JSONResponse(
                status_code=400,
                content={"detail": f"Unknown country: {name}"},
            )
        if name not in cleaned:
            cleaned.append(name)
    set_target_countries(cleaned)
    return {"ok": True, "countries": cleaned}


@router.delete("/llm/key")
async def clear_llm_key() -> dict:
    """Forget the runtime key (in memory and in backend/.env). Falls back
    to whatever GEMINI_API_KEY the environment provides."""
    settings.clear_api_key_runtime()
    return {"ok": True}


class _SearchSourcePayload(BaseModel):
    source: str


@router.put("/search-source")
async def put_search_source(payload: _SearchSourcePayload) -> dict:
    """Switch the LinkedIn search implementation (design §3). Invalid
    values are rejected rather than coerced, so a typo in the UI or API
    can never silently pick a source the user did not mean."""
    value = (payload.source or "").strip().lower()
    if value not in ("mcp", "playwright"):
        return JSONResponse(
            status_code=400,
            content={"detail": "source must be 'mcp' or 'playwright'"},
        )
    settings.search_source = value
    reset_source_registry()  # next get_linkedin_source() builds the new one
    return {"source": settings.effective_search_source()}


@router.post("/linkedin/check")
async def check_linkedin() -> dict:
    """Live session canary: spawn the MCP container, initialize, call one
    read-only tool, classify the outcome. Slow (container cold-start), so
    it only ever runs on an explicit click — never as part of page load."""
    source = get_linkedin_source()
    try:
        result = await source.check_health()
    except Exception as exc:  # the canary must always answer, even on a crash
        result = {"status": "unavailable", "detail": repr(exc)[:300]}
    finally:
        await source.close()
    return result
