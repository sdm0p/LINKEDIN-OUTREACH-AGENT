"""Settings page endpoints: LLM provider, LinkedIn MCP prerequisites,
draft cap, and data-retention info.

Full cookie health checking arrives with the live validation pass; the
status shown here reflects cheap local prerequisites so the UI is honest
about what is known without spawning a browser.
"""

from fastapi import APIRouter

from ..config import settings
from ..llm import provider_info
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
        "linkedin": {
            "status": status,
            "detail": detail,
            "docker_installed": prereq["docker_installed"],
            "session_dir_present": prereq["session_dir_present"],
        },
        "search_source": {"name": "linkedin", "available_sources": ["linkedin"]},
        "drafts": {
            "max_per_day": settings.max_drafts_per_day,
            "sent_today": 0,
        },
        "retention": {
            **retention,
            "resume_cached": resume_state.has_resume,
            "drafts_total": len(all_drafts),
            "drafts_new": len(drafts_new),
        },
    }
