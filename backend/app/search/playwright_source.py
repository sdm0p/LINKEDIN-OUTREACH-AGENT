"""PlaywrightSource — direct browser automation of the user's own LinkedIn
session. The destination search source (playwright-source-design.md); MCP
is demoted to a capability fallback delegated inside this class.

Status on this branch: SKELETON. search_posts raises until item 5 lands;
check_health reports honestly that the browser is not implemented yet;
search_people and job-detail enrichment already delegate to the MCP source
(the design's v1 fallback), so the seam can be selected today without
breaking DM resolution or job-detail fetches.

The class satisfies SearchSource (search/base.py) so callers never know
which implementation is behind the seam.
"""

from typing import Any

from .base import Post, Recency, SearchResult  # noqa: F401 (contract types)


class PlaywrightSource:
    """In-process Playwright automation of the logged-in LinkedIn session.

    Session model (design §4): a persistent browser profile under
    settings.pw_profile_dir, created by the one-time pw_login flow — never
    the MCP container's session volume (different browser, different
    profile format)."""

    name = "linkedin"

    def __init__(self) -> None:
        self._mcp: Any | None = None  # lazily-created MCP delegate

    # ---------- the MCP capability fallback (design §8) ----------

    def _mcp_delegate(self) -> Any:
        """Lazily create the MCP source used ONLY for the capabilities
        Playwright does not implement yet (search_people, job details).
        Never used for search_posts — a Playwright search failure fails
        loudly instead of double-routing and hiding bugs."""
        if self._mcp is None:
            from .linkedin_mcp import LinkedInMCPSource

            self._mcp = LinkedInMCPSource()
        return self._mcp

    async def search_people(self, query: str) -> list[dict[str, Any]]:
        """DM-target lookup: delegated to MCP until v2 (no ceiling problem,
        tiny volume, only fires while qualifying a lead)."""
        return await self._mcp_delegate().search_people(query)

    async def get_job_details(self, job_id: str) -> dict[str, Any]:
        """Queue enrichment ('Fetch job details'): delegated to MCP until
        v2 — an explicit, per-click action, never run automatically."""
        return await self._mcp_delegate().get_job_details(job_id)

    async def search_job_ids(
        self, keyword: str, location: str | None = None
    ) -> list[dict[str, str]]:
        """Keyword-side job search for enrichment fallback: delegated to
        MCP until v2 (same explicit-click contract as get_job_details)."""
        return await self._mcp_delegate().search_job_ids(keyword, location)

    # ---------- native capabilities (land later on this branch) ----------

    async def search_posts(self, keyword: str, recency: Recency) -> SearchResult:
        """Search the LinkedIn Posts tab directly (design §5-§6): navigate
        to the search URL, run the bounded scroll loop, extract per-card
        into Post. NOT IMPLEMENTED YET — lands as branch item 5. Raising
        here is deliberate: a misconfigured SEARCH_SOURCE=playwright must
        fail loudly, never silently degrade to the MCP search path."""
        raise NotImplementedError(
            "PlaywrightSource.search_posts is not implemented yet (branch "
            "item 5). Set SEARCH_SOURCE=mcp to use the container source."
        )

    async def check_health(self) -> dict[str, str]:
        """Session health for the Settings badge. Honest until the browser
        flow lands: 'not_configured' when no profile dir exists yet, never
        a fabricated 'valid'."""
        from ..config import settings

        profile = settings.pw_profile_dir
        if not profile.exists() or not any(profile.iterdir()):
            return {
                "status": "not_configured",
                "detail": "No Playwright browser profile yet — the one-time "
                "pw_login flow arrives with branch item 5.",
            }
        return {
            "status": "unavailable",
            "detail": "Playwright browser automation is not implemented yet "
            "(branch item 5).",
        }

    async def close(self) -> None:
        """Release the browser and any MCP delegate. Safe to call twice."""
        if self._mcp is not None:
            await self._mcp.close()
            self._mcp = None
