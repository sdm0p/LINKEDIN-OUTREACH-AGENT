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
    normalization missed — important until the live schema is validated."""

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
    """Default source (registered lazily so importing the module never
    requires Docker to be installed)."""
    if SOURCE_NAME not in registry.names():
        from .linkedin_mcp import LinkedInMCPSource

        registry.register(LinkedInMCPSource())
    return registry.get(SOURCE_NAME)
