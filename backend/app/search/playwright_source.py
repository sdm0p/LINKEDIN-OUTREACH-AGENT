"""PlaywrightSource — direct browser automation of the user's own LinkedIn
session. The destination search source (playwright-source-design.md); MCP
is demoted to a capability fallback delegated inside this class.

Search flow (design §5-§6): navigate to the Posts-tab search URL, scroll
with human pacing while the feed keeps growing, collect per-card text
blobs + links in the browser, and parse them into Post records in pure
Python (pw_flow.parse_card_blob — the fixture-testable surface). Raw card
blobs are saved as fixtures through the same harness as MCP payloads.

Everything browser-dependent is isolated behind the duck-typed `page`
object (goto/evaluate/mouse.wheel): tests drive the full search loop with
a fake page, so the ONLY untested code is the real browser launch — which
is exactly the live-login gate (one-time pw_login, dummy account).

Session model: a persistent Chromium profile under settings.pw_profile_dir
(created by `uv run python -m app.search.pw_login`), headed by default.
Never the MCP container's session volume.
"""

import asyncio
import random
import re
from typing import Any

from .base import Post, Recency, SearchError, SearchResult
from . import pw_flow

# Cards collected per round: [{text, hrefs}], gathered in the browser.
# Selector list is ordered most-specific first; the first present wins.
# innerText is truncated per card — a hiring post never needs more, and
# a truncated blob is exactly what the sanity gate exists to catch.
# Card collection WITHOUT class selectors. LinkedIn search results use
# hashed CSS-module class names (verified live 2026-09: "b3361fd9
# _398a5a3d") that rotate — class-based collection was exactly the
# selector-rot trap. The stable contract is TEXT: every rendered post card
# contains the literal marker "Feed post". We take the minimal enclosing
# elements (no descendant also matching), so nested wrappers collapse to
# one blob per post.
_COLLECT_CARDS_JS = """
() => {
  const MARKER = 'Feed post';
  const candidates = [];
  for (const el of document.querySelectorAll('div, article, li, section')) {
    const t = el.innerText || '';
    if (t.includes(MARKER) && t.length < 6000) candidates.push(el);
  }
  const set = new Set(candidates);
  const cards = [];
  for (const el of candidates) {
    let minimal = true;
    for (const d of el.querySelectorAll('*')) {
      if (set.has(d)) { minimal = false; break; }
    }
    if (minimal) {
      cards.push({
        text: el.innerText.slice(0, 4000),
        hrefs: Array.from(el.querySelectorAll('a[href]'))
          .map((a) => a.getAttribute('href'))
          .filter(Boolean)
          .slice(0, 25),
      });
    }
  }
  return cards.slice(0, 200);
}
"""

_BODY_TEXT_JS = "() => document.body ? document.body.innerText.slice(0, 3000) : ''"

_JOB_ID_RE = re.compile(r"/jobs/view/(?:[^/?#]*-)?(\d+)")


def _absolutize(href: str) -> str:
    return href if href.startswith("http") else f"https://www.linkedin.com{href}"


def attach_links(post: Post, hrefs: list[str]) -> None:
    """Map a card's hrefs onto the Post: permalink, author profile, job card."""
    for href in hrefs or []:
        if not post.post_url and ("/posts/" in href or "/feed/update/" in href):
            post.post_url = _absolutize(href)
        elif not post.author_profile_url and "/in/" in href:
            post.author_profile_url = _absolutize(href)
        elif "job_id" not in post.raw and "/jobs/view/" in href:
            match = _JOB_ID_RE.search(href)
            post.raw["job_id"] = match.group(1) if match else ""
            post.raw["job_url"] = _absolutize(href)


def dedupe_blobs(blobs: list[str]) -> list[str]:
    """Drop card blobs contained inside another card blob (nested parent
    containers re-reporting their children). Order-preserving."""
    kept: list[str] = []
    for blob in blobs:
        if any(blob in other for other in blobs if other != blob):
            continue
        if blob not in kept:
            kept.append(blob)
    return kept


def parse_card_fixture(fixture: dict) -> list[Post]:
    """Parse a saved playwright-cards fixture (the pw_replay surface).
    The exact same dedupe → parse → link-attach path as the live search,
    so a selector fix applies to old captures instantly."""
    cards = [c for c in (fixture.get("cards") or []) if isinstance(c, dict)]
    blobs = dedupe_blobs([str(c.get("text") or "") for c in cards])
    posts: list[Post] = []
    for blob in blobs:
        post = pw_flow.parse_card_blob(blob)
        if post is None:
            continue
        for card in cards:
            if str(card.get("text") or "") == blob:
                attach_links(post, [str(h) for h in card.get("hrefs") or []])
                break
        posts.append(post)
    return posts


class PlaywrightSource:
    """In-process Playwright automation of the logged-in LinkedIn session."""

    name = "linkedin"

    # Scroll policy (design §6): the 3-post ceiling dies here. v1 target is
    # the agreed first-step 15/keyword; the guarded exhaustion tuning is
    # branch item 7. Two consecutive no-growth checks stop the loop —
    # virtualization tolerance — and pw_flow's fuse is the circuit breaker.
    MAX_POSTS = 15
    STABLE_CHECKS = 2
    MAX_ROUNDS = 40

    def __init__(self) -> None:
        self._mcp: Any | None = None
        self._pw: Any | None = None
        self._context: Any | None = None
        self._page: Any | None = None

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

    # ---------- browser lifecycle ----------

    async def _launch_page(self) -> Any:
        """Launch the persistent-profile browser. The one place playwright
        is imported — lazily, so the MCP path never needs the package."""
        try:
            from playwright.async_api import async_playwright  # noqa: PLC0415
        except ImportError as exc:
            raise SearchError(
                "playwright is not installed — run `uv add playwright` and "
                "`uv run playwright install chromium`, then the one-time "
                "`uv run python -m app.search.pw_login`"
            ) from exc
        from ..config import settings

        settings.pw_profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = await async_playwright().start()
        # Same channel ladder as the login CLI: reuse the profile in the
        # SAME browser brand it was created with, so cookies and
        # fingerprint stay consistent (a mismatched brand re-prompts).
        self._context = None
        last_error: Exception | None = None
        for channel in pw_flow.CHANNEL_CANDIDATES:
            kwargs = {
                "user_data_dir": str(settings.pw_profile_dir),
                "headless": settings.pw_headless,
                "viewport": {"width": 1440, "height": 900},
                "args": list(pw_flow.STEALTH_ARGS),
            }
            if channel:
                kwargs["channel"] = channel
            try:
                self._context = await self._pw.chromium.launch_persistent_context(
                    **kwargs
                )
                break
            except Exception as exc:  # noqa: BLE001 — try the next channel
                last_error = exc
                self._context = None
        if self._context is None:
            raise SearchError(
                f"Could not launch a browser (tried chrome/msedge/chromium): "
                f"{last_error}"
            )
        self._page = (
            self._context.pages[0]
            if self._context.pages
            else await self._context.new_page()
        )
        return self._page

    async def _page_for_work(self) -> Any:
        return self._page or await self._launch_page()

    # ---------- search ----------

    async def search_posts(
        self, keyword: str, recency: Recency, *, _page: Any = None
    ) -> SearchResult:
        """Search the LinkedIn Posts tab directly.

        _page injects a duck-typed page (tests); production launches the
        persistent-profile browser. A search failure raises — never a
        silent fallback to MCP."""
        page = _page or await self._page_for_work()
        url = pw_flow.posts_search_url(keyword, recency)

        try:
            await page.goto(url, timeout=45_000, wait_until="domcontentloaded")
            body_text = await page.evaluate(_BODY_TEXT_JS) or ""
        except Exception as exc:  # navigation/evaluate failures -> SearchError
            raise SearchError(
                f"Could not load LinkedIn search: {str(exc)[:200]}"
            ) from exc

        lowered = (body_text or "").lower()
        page_url = (getattr(page, "url", "") or "").lower()
        if "authwall" in page_url or "/login" in page_url or "sign in" in lowered[:800]:
            raise SearchError(
                "LinkedIn session expired — run the one-time pw_login flow "
                "(uv run python -m app.search.pw_login)"
            )

        collected: dict[str, dict] = {}  # blob-key -> {text, hrefs}
        prev = 0
        stable = 0
        rounds = 0
        stop_reason = ""
        while True:
            cards = await page.evaluate(_COLLECT_CARDS_JS) or []
            for card in cards:
                text = str(card.get("text") or "")
                key = text[:200]
                if key and key not in collected:
                    collected[key] = {
                        "text": text,
                        "hrefs": [str(h) for h in card.get("hrefs") or []],
                    }

            if len(collected) > prev:
                stable = 0
            else:
                stable += 1
            prev = len(collected)

            decision = pw_flow.scroll_decision(
                rounds_without_growth=stable,
                total_cards=prev,
                stable_checks=self.STABLE_CHECKS,
                max_posts=self.MAX_POSTS,
            )
            if decision.stop:
                stop_reason = decision.reason
                break
            rounds += 1
            if rounds >= self.MAX_ROUNDS:
                stop_reason = "round limit"
                break

            # Human pacing within the keyword: jittered wheel deltas and
            # read-pauses, never a metronome (design §5 risk mitigations).
            try:
                await page.mouse.wheel(0, random.randint(900, 1500))
            except Exception:
                pass  # a wheel hiccup must not kill the search
            await asyncio.sleep(random.uniform(1.2, 2.8))

        cards_payload = list(collected.values())
        posts = parse_card_fixture({"cards": cards_payload})
        # The cap truncates too — a person who stopped at 15 didn't read
        # cards 16-20. Whatever scrolled in during the final round over the
        # target is dropped before filters, keeping runs predictable.
        if len(posts) > self.MAX_POSTS:
            posts = posts[: self.MAX_POSTS]

        # Zero cards from a rendered search page is treated as selector
        # rot, not a quiet empty result — a false alarm costs a trace
        # line, silent rot poisons runs (the exact lesson the MCP era
        # taught). The fixture captures the evidence either way.
        degraded = not posts

        self._maybe_save_fixture(keyword, recency, cards_payload, degraded)
        return SearchResult(
            posts=posts,
            raw_hit_count=len(posts),
            degraded=degraded,
            trace_note=f"scroll: {stop_reason}" if stop_reason else None,
        )

    def _maybe_save_fixture(
        self, keyword: str, recency: str, cards: list[dict], degraded: bool
    ) -> None:
        """Fixture harness (design §6): the same saver as MCP payloads, so
        selector iteration runs offline. Never raises, never writes when
        SAVE_FIXTURES is off."""
        try:
            from ..config import settings
            from . import fixtures

            mode = settings.effective_save_fixtures()
            if fixtures.should_save(mode, degraded):
                fixtures.save_fixture(
                    source="pw",
                    keyword=keyword,
                    recency=recency,
                    payload={
                        "kind": "playwright-cards",
                        "keyword": keyword,
                        "recency": recency,
                        "cards": cards,
                    },
                    kind="playwright-cards",
                )
        except Exception:  # noqa: BLE001 — the harness must never break search
            pass

    # ---------- health ----------

    async def check_health(self) -> dict[str, str]:
        """Session health: profile present + feed loads without a login
        wall -> valid. Honest about every failure mode."""
        from ..config import settings

        profile = settings.pw_profile_dir
        if not profile.exists() or not any(profile.iterdir()):
            return {
                "status": "not_configured",
                "detail": "No Playwright browser profile yet — run the "
                "one-time login: uv run python -m app.search.pw_login",
            }
        try:
            page = await self._page_for_work()
            await page.goto(
                "https://www.linkedin.com/feed/",
                timeout=45_000,
                wait_until="domcontentloaded",
            )
            body_text = (await page.evaluate(_BODY_TEXT_JS) or "").lower()
        except SearchError as exc:
            return {"status": "unavailable", "detail": str(exc)[:300]}
        except Exception as exc:
            return {
                "status": pw_flow.classify_browser_failure(str(exc)),
                "detail": str(exc)[:300],
            }
        page_url = (getattr(page, "url", "") or "").lower()
        if (
            "authwall" in page_url
            or "/login" in page_url
            or "sign in" in body_text[:600]
        ):
            return {
                "status": "expired",
                "detail": "LinkedIn shows a login wall — the session cookie "
                "is dead. Run uv run python -m app.search.pw_login again.",
            }
        return {
            "status": "valid",
            "detail": "Browser session is live and sees the logged-in feed.",
        }

    async def close(self) -> None:
        """Release the browser and MCP delegate. Safe to call twice."""
        if self._context is not None:
            try:
                await self._context.close()
            except Exception:  # noqa: BLE001
                pass
            self._context = None
            self._page = None
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:  # noqa: BLE001
                pass
            self._pw = None
        if self._mcp is not None:
            await self._mcp.close()
            self._mcp = None
