"""Search source abstraction (plan: multi-source interface).

The pipeline only talks to SearchSource; adding Naukri/Indeed later means
adding a module here and registering it — nothing downstream changes.
"""

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

Recency = Literal["24h", "week", "month"]

SOURCE_NAME = "linkedin"


@dataclass
class Post:
    """A normalized hiring-post record. `raw` keeps the source's original
    fields so later stages (YoE filter, extraction) can recover anything the
    normalization missed — important until the live schema is validated.

    The LinkedIn source fills raw["job_id"]/raw["job_url"] when the post
    carries an attached job card, and post_url with the post's own
    permalink when the response provides one."""

    post_id: str
    text: str
    post_url: str = ""
    author_name: str = ""
    author_headline: str = ""
    author_profile_url: str = ""
    posted_at: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchResult:
    posts: list[Post]
    raw_hit_count: int  # what the source reported, before dedup/filtering
    degraded: bool = False  # True when response looked empty/garbage


class SearchError(RuntimeError):
    """Raised when the source cannot be reached or the session is unusable."""


class SearchSource(Protocol):
    name: str

    async def search_posts(self, keyword: str, recency: Recency) -> SearchResult: ...

    async def search_people(self, query: str) -> list[dict[str, Any]]:
        """Fallback lookup for a DM target when no email is found."""
        ...

    async def check_health(self) -> dict[str, str]:
        """Return {"status": valid|degraded|expired|unavailable, "detail": ...}."""
        ...


class SourceRegistry:
    """Maps source name -> instance. The pipeline looks sources up here."""

    def __init__(self) -> None:
        self._sources: dict[str, Any] = {}

    def register(self, source: Any) -> None:
        self._sources[source.name] = source

    def get(self, name: str) -> Any:
        source = self._sources.get(name)
        if source is None:
            raise SearchError(
                f"Search source '{name}' is not registered. "
                f"Available: {sorted(self._sources) or 'none'}"
            )
        return source

    def names(self) -> list[str]:
        return sorted(self._sources)


registry = SourceRegistry()


def get_linkedin_source() -> Any:
    """The linkedin seam. The implementation is picked by the SEARCH_SOURCE
    setting:

    - "mcp" (code default for now): LinkedInMCPSource — one spawned
      container per run over stdio.
    - "playwright": PlaywrightSource — in-process browser automation of the
      user's own session; the destination default, flipped at the merge
      gate's container gate (see playwright-source-design.md §3/§10).

    Registered lazily so importing this module never requires Docker or
    playwright to be installed, and cached in the registry until
    reset_source_registry() (tests and tooling)."""
    from ..config import settings

    if SOURCE_NAME in registry.names():
        return registry.get(SOURCE_NAME)
    if settings.effective_search_source() == "playwright":
        from .playwright_source import PlaywrightSource

        registry.register(PlaywrightSource())
    else:
        from .linkedin_mcp import LinkedInMCPSource

        registry.register(LinkedInMCPSource())
    return registry.get(SOURCE_NAME)


def reset_source_registry() -> None:
    """Drop cached sources (test isolation, config reload in tooling)."""
    registry._sources.clear()
