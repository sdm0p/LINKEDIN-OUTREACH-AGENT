"""PlaywrightSource tests (branch item 6, design §5-§6).

The pure helpers (URL builder, card parser, scroll decision, failure
classification) are tested directly. The full search loop is driven with
a duck-typed FakePage — production injects a real Playwright page, tests
inject this — so every behavior except the actual browser launch is
exercised offline. No network, no browser, no login.
"""

import json

import pytest

from app.search import pw_flow
from app.search import fixtures as fx
from app.search.base import Post, SearchError
from app.search.playwright_source import (
    PlaywrightSource,
    attach_links,
    dedupe_blobs,
    parse_card_fixture,
)


# ---------- URL builder ----------


def test_posts_search_url_keyword_goes_out_literal():
    url = pw_flow.posts_search_url("hiring developer", "24h")
    assert "linkedin.com/search/results/content/" in url
    assert "keywords=hiring%20developer" in url  # quote() encodes space as %20
    assert "past-24h" in url


def test_posts_search_url_never_appends_country():
    """The geo-scoping lesson: appending the country kills city-only and
    bare-Remote posts. The URL builder must not reintroduce the bug."""
    url = pw_flow.posts_search_url("hiring developer", "24h")
    assert "india" not in url.lower()


# ---------- card parser ----------


CARD = (
    "Aravind Kumar\n• 3rd+\nTechnical Recruiter at Gios Technology\n19h •\n"
    "Follow\nWe are Hiring: Training Developer | Onsite\n"
    "📍 Location: Bangalore, India\nExperience: 2+ Years\n"
    "Email aravind.rg@gmail.com with your CV\n42"
)


def test_parse_card_blob_extracts_fields():
    post = pw_flow.parse_card_blob(CARD)
    assert post is not None
    assert post.author_name == "Aravind Kumar"
    # "Follow" must never be mistaken for the headline (link, not title).
    assert post.author_headline == "Technical Recruiter at Gios Technology"
    assert post.posted_at  # "19h •" parsed
    assert "hiring" in post.text.lower()
    assert not post.text.strip().endswith("42")  # reaction line dropped
    assert len(post.post_id) == 24


def test_parse_card_blob_stable_ids():
    a = pw_flow.parse_card_blob(CARD)
    b = pw_flow.parse_card_blob(CARD)
    assert a.post_id == b.post_id


def test_parse_card_blob_no_body_returns_none():
    assert pw_flow.parse_card_blob("Someone\nFollow\n42") is None


def test_parse_card_blob_empty_blob_returns_none():
    assert pw_flow.parse_card_blob("") is None


def test_parse_posted_at_variants():
    assert pw_flow.parse_posted_at("3h •")
    assert pw_flow.parse_posted_at("2d •")
    assert pw_flow.parse_posted_at("1mo •")
    assert pw_flow.parse_posted_at("just now") == ""


# ---------- scroll decision ----------


def test_scroll_stops_at_cap():
    d = pw_flow.scroll_decision(
        rounds_without_growth=0, total_cards=15, stable_checks=2, max_posts=15
    )
    assert d.stop and "cap" in d.reason and not d.exhausted


def test_scroll_stops_after_stable_checks():
    d = pw_flow.scroll_decision(
        rounds_without_growth=2, total_cards=9, stable_checks=2, max_posts=15
    )
    assert d.stop and d.exhausted  # feed end, not cap


def test_scroll_continues_while_growing():
    d = pw_flow.scroll_decision(
        rounds_without_growth=0, total_cards=5, stable_checks=2, max_posts=15
    )
    assert not d.stop


def test_cap_is_the_tighter_bound_when_below_fuse():
    d = pw_flow.scroll_decision(
        rounds_without_growth=0, total_cards=80, stable_checks=0, max_posts=15
    )
    assert d.stop and "cap" in d.reason


def test_fuse_is_the_circuit_breaker():
    """The fuse only matters when the target exceeds it (item 7's
    exhaustion tuning): at a 200 target, 80 cards is where it breaks."""
    d = pw_flow.scroll_decision(
        rounds_without_growth=0, total_cards=80, stable_checks=0, max_posts=200
    )
    assert d.stop and d.exhausted


# ---------- failure classification ----------


def test_classify_browser_failure_auth_shaped():
    assert pw_flow.classify_browser_failure("Sign in to continue") == "expired"
    assert pw_flow.classify_browser_failure("checkpoint challenge") == "expired"


def test_classify_browser_failure_other():
    assert pw_flow.classify_browser_failure("net::ERR_TIMED_OUT") == "unavailable"


# ---------- blob/link helpers ----------


def test_dedupe_blobs_drops_nested_children():
    parent = "line1\nline2\nline3"
    child = "line2"
    assert dedupe_blobs([parent, child]) == [parent]


def test_attach_links_maps_all_three():
    post = Post(post_id="x" * 24, text="body")
    attach_links(
        post,
        [
            "/in/rita/",
            "/posts/rita_acme-activity-123-AbCd",
            "/jobs/view/4464542122/",
        ],
    )
    assert post.author_profile_url.endswith("/in/rita/")
    assert post.post_url.endswith("/posts/rita_acme-activity-123-AbCd")
    assert post.raw["job_id"] == "4464542122"


def test_parse_card_fixture_roundtrip():
    fixture = {
        "cards": [
            {
                "text": CARD,
                "hrefs": ["/in/aravind-kumar/", "/posts/aravind-activity-1-X"],
            }
        ]
    }
    posts = parse_card_fixture(fixture)
    assert len(posts) == 1
    assert posts[0].author_name == "Aravind Kumar"
    assert posts[0].post_url.endswith("/posts/aravind-activity-1-X")


# ---------- the search loop (FakePage) ----------


class FakeMouse:
    def __init__(self):
        self.wheels = []

    async def wheel(self, dx, dy):
        self.wheels.append((dx, dy))


class FakePage:
    """Duck-typed Playwright page: goto + evaluate + mouse.wheel, with a
    scripted card sequence. Each evaluate('collect') call returns the next
    snapshot, simulating the feed growing then exhausting."""

    def __init__(self, cards_per_round: list[list[dict]], body_text="feed"):
        self._rounds = cards_per_round
        self._i = 0
        self.url = "https://www.linkedin.com/search/results/content/"
        self.mouse = FakeMouse()
        self._body = body_text
        self.gotos: list[str] = []

    async def goto(self, url, timeout=None, wait_until=None):
        self.gotos.append(url)

    async def evaluate(self, script, *args):
        if "body.innerText" in script or "document.body" in script:
            return self._body
        if self._i < len(self._rounds):
            cards = self._rounds[self._i]
            self._i += 1
            return cards
        return []

    def _card(self, name: str, age: str, body: str) -> dict:
        return {
            "text": f"{name}\nRecruiter at Acme\n{age}\nFollow\n{body}",
            "hrefs": [f"/posts/{name.lower().replace(' ', '-')}-activity-1-X"],
        }


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    return tmp_path


@pytest.mark.asyncio
async def test_search_collects_beyond_mcp_ceiling(isolated):
    """The point of the whole branch: more than 3 posts per keyword."""
    page = FakePage(
        [
            [FakePage._card(None, "", "")] if False else
            [
                {"text": f"Recruiter {i}\nTalent at Acme\n3h •\nFollow\n"
                         f"We are hiring engineer number {i} for the platform team. DM me.",
                 "hrefs": []}
                for i in range(8)
            ],
            [
                {"text": f"Recruiter {i}\nTalent at Acme\n3h •\nFollow\n"
                         f"We are hiring engineer number {i} for the platform team. DM me.",
                 "hrefs": []}
                for i in range(15)
            ],
            [],  # feed stops growing
            [],
        ]
    )
    result = await PlaywrightSource().search_posts("kw", "24h", _page=page)
    assert result.raw_hit_count == 15
    assert not result.degraded
    assert "exhausted" in (result.trace_note or "") or "cap" in (result.trace_note or "")
    assert len(page.mouse.wheels) >= 1  # the loop actually scrolled


@pytest.mark.asyncio
async def test_search_stops_at_target_cap(isolated):
    page = FakePage(
        [
            [
                {"text": f"Recruiter {i}\nTalent at Acme\n3h •\nFollow\n"
                         f"We are hiring engineer number {i} for the platform team. DM me.",
                 "hrefs": []}
                for i in range(20)
            ]
        ]
    )
    result = await PlaywrightSource().search_posts("kw", "24h", _page=page)
    assert result.raw_hit_count == PlaywrightSource.MAX_POSTS == 15
    assert "cap" in (result.trace_note or "")


@pytest.mark.asyncio
async def test_search_login_wall_raises(isolated):
    page = FakePage([], body_text="Sign in to continue to LinkedIn")
    page.url = "https://www.linkedin.com/authwall"
    with pytest.raises(SearchError, match="pw_login"):
        await PlaywrightSource().search_posts("kw", "24h", _page=page)


@pytest.mark.asyncio
async def test_search_navigation_failure_raises_searcherror(isolated):
    class DeadPage:
        url = ""

        async def goto(self, *a, **k):
            raise TimeoutError("too slow")

        async def evaluate(self, *a, **k):
            return ""

    with pytest.raises(SearchError, match="Could not load"):
        await PlaywrightSource().search_posts("kw", "24h", _page=DeadPage())


@pytest.mark.asyncio
async def test_search_saves_fixture_when_enabled(isolated, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "save_fixtures", "always")
    cards = [
        {
            "text": "Recruiter Rita\nTalent at Acme\n3h •\nFollow\n"
            "We are hiring a Backend Engineer for our platform team. DM me.",
            "hrefs": ["/in/rita/"],
        }
    ]
    page = FakePage([cards, [], []])
    await PlaywrightSource().search_posts("kw", "24h", _page=page)
    files = fx.load_fixtures()
    assert len(files) == 1
    saved = json.loads(files[0].read_text(encoding="utf-8"))
    assert saved["kind"] == "playwright-cards"
    assert parse_card_fixture(saved)[0].author_name == "Recruiter Rita"


@pytest.mark.asyncio
async def test_search_empty_feed_flags_degraded(isolated):
    page = FakePage([[], []])
    result = await PlaywrightSource().search_posts("kw", "24h", _page=page)
    assert result.raw_hit_count == 0
    assert result.degraded  # selector rot alarms, never a quiet zero


# ---------- health without any browser ----------


@pytest.mark.asyncio
async def test_health_not_configured_without_profile(monkeypatch, tmp_path):
    from app.config import settings

    monkeypatch.setattr(settings, "pw_profile_dir", tmp_path / "absent")
    health = await PlaywrightSource().check_health()
    assert health["status"] == "not_configured"
