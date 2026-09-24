"""Pure helpers for the Playwright search flow (branch item 6).

Everything here is deterministic Python: URL construction, per-card text
parsing, the scroll-stop decision, and auth-failure classification. The
browser orchestration in playwright_source.py calls these; tests run them
without any browser (playwright-source-design.md §6: selector iteration
must cost zero sessions).

Extraction model: the browser collects one "card blob" per rendered post
(a truncated innerText of the card element). These blobs are parsed HERE,
in pure Python, so the parse logic is the fixture-testable surface — and
the raw blobs are saved as fixtures for offline replay.

Card-blob layout (LinkedIn Posts search card, text order):
    <Author name>            — followed by degree/connection noise
    <Headline>               — the author's title line ("X at Y")
    <age> •                  — relative timestamp ("19h •", "5d •")
    <post body...>           — may end with "…more"
    <reaction/comment lines> — "42", "17 comments", trailing counts

Parsing rules learned from the live MCP contract (kept deliberately
aligned with linkedin_mcp.parse_search_payload):
- Age line: r"^(\\d+)\\s?(m|h|d|w|mo)\\s?•$" → posted_at approximation.
- Degree lines ("• 3rd+") and known noise lines are dropped.
- A link ("Follow", "…more") is never mistaken for the author's headline
  (the "link, not title" pitfall) — short leading lines that match link
  phrases are skipped, and the headline must come from the card's
  header region only.
"""

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from .base import Post

# Relative ages as LinkedIn renders them on the Posts tab. The trailing
# bullet is optional and the line may carry an "Edited" suffix — live DOM
# shows "8h" and "6h • Edited" (verified live 2026-09).
RELATIVE_AGE = re.compile(
    r"^(\d+)\s?(m|h|d|w|mo)(?:\s?\u2022)?(?:\s*\u2022?\s*Edited)?$"
)
_AGE_UNITS = {
    "m": "minutes",
    "h": "hours",
    "d": "days",
    "w": "weeks",
}

SKIP_LINES = {
    "",
    "\u200b",
    "Feed post",  # the card-separator marker itself (stripped from blobs)
    "Join",  # connection-request button label rendered as a text line
    "Follow",
    "Following",
    "\u2026more",
    "\u2026 more",
    "more",
    "Send message",
    "View feed post",
    "Repost",
    "Like",
    "Comment",
    "Share",
    "Send",
    "Are these results helpful?",
}

DEGREE_LINE = re.compile(r"\u2022?\s*(1st|2nd|3rd\+?)\s*\u2022?")
# Lines that are pure engagement noise when seen at a card's tail.
REACTION_LINE = re.compile(r"^(\d+[,\d]*)\s*(?:reactions|comments|reposts)?\s*$")

# Job-card noise inside an attached job card (same markers as the MCP
# parser — cut the body at the earliest marker).
CARD_MARKERS = (
    "Actively reviewing applicants",
    "View job",
    "(Verified job)",
    "Are these results helpful?",
)


def posts_search_url(keyword: str, recency: str) -> str:
    """The LinkedIn Posts-tab search URL for one keyword + recency.

    The keyword goes out as written — no country appended. LinkedIn's
    search is literal text matching, so "hiring developer India" would
    never surface city-only posts ("Bangalore") or bare-Remote posts;
    geo targeting is the ingest-side location filter's job (the exact
    lesson from the geo-scoping fix on main)."""
    from urllib.parse import quote

    dates = {"24h": "past-24h", "week": "past-week", "month": "past-month"}
    date_value = dates.get(recency, "past-24h")
    return (
        "https://www.linkedin.com/search/results/content/"
        f"?keywords={quote(keyword)}&datePosted=%22{date_value}%22"
    )


def parse_posted_at(raw: str) -> str:
    """'19h •' -> an ISO timestamp approximating the post's real age.

    LinkedIn gives no timezone, so the age counts back from now in UTC —
    good enough to sort a queue by recency. Unparseable -> '' (unknown).
    """
    match = RELATIVE_AGE.match((raw or "").strip())
    if not match:
        return ""
    amount, unit = int(match.group(1)), match.group(2)
    if unit == "mo":
        kwargs = {"days": amount * 30}  # timedelta has no months
    else:
        kwargs = {_AGE_UNITS[unit]: amount}
    try:
        return (datetime.now(UTC) - timedelta(**kwargs)).isoformat(timespec="minutes")
    except (OverflowError, ValueError):
        return ""


# Group-post cards embed their activity URN in a highlight link:
# /groups/<id>/?...highlightedUpdateUrn=urn%3Ali%3Aactivity%3A<19 digits>
# Member posts expose NO permalink in the new search DOM (the old age-
# anchor link is gone — the age renders as plain text, verified live
# 2026-09), so recovery is partial by design: group posts get a real
# permalink, member posts stay empty until a v2 enrichment pass can
# look them up on the author's activity page.
_GROUP_URN = re.compile(r"urn(?:%3A|:)li(?:%3A|:)activity(?:%3A|:)(\d{15,})")


def permalink_from_hrefs(hrefs: list[str]) -> str:
    """Recover a post permalink from a card's hrefs when possible."""
    for href in hrefs or []:
        match = _GROUP_URN.search(href or "")
        if match:
            return f"https://www.linkedin.com/feed/update/urn:li:activity:{match.group(1)}/"
    return ""


def _clean_lines(blob: str) -> list[str]:
    cleaned: list[str] = []
    for line in (blob or "").splitlines():
        line = line.replace("\u200b", "").strip()
        if line in SKIP_LINES:
            continue
        cleaned.append(line)
    return cleaned


def parse_card_blob(blob: str) -> Post | None:
    """Parse one card's text blob into a Post.

    Returns None when the blob carries no post body (UI fragments, an
    empty header) — the caller counts that as a parse miss.
    """
    lines = _clean_lines(blob)
    lines = [ln for ln in lines if not DEGREE_LINE.fullmatch(ln)]
    if not lines:
        return None

    author_name = lines[0]

    headline = ""
    posted_at = ""
    body_start = 0
    for i, line in enumerate(lines[1:6], start=1):
        age_match = RELATIVE_AGE.match(line)
        if age_match:
            posted_at = parse_posted_at(line)
            body_start = i + 1
            break
        if not headline and not REACTION_LINE.match(line):
            headline = line
            body_start = i + 1
    if body_start == 0:
        body_start = min(1, len(lines))

    body = "\n".join(lines[body_start:]).strip()

    # Cut attached-job-card noise at the earliest marker (the attached
    # job's own details arrive through explicit enrichment, not text).
    cuts = [idx for idx in (body.find(m) for m in CARD_MARKERS) if idx != -1]
    if cuts:
        idx = min(cuts)
        nl = body.rfind("\n", 0, idx)
        body = (body[:nl] if nl != -1 else "").strip()
    if body.endswith("\u2026more"):
        body = body[: -len("\u2026more")].strip()
    elif body.endswith("\u2026 more"):
        body = body[: -len("\u2026 more")].strip()

    # Drop trailing reaction/comment lines (bare counts, "42 comments").
    body_lines = body.splitlines()
    while body_lines and REACTION_LINE.match(body_lines[-1].strip()):
        body_lines.pop()
    body = "\n".join(body_lines).strip()

    if not body:
        return None

    return Post(
        post_id=hashlib.sha256(body.encode("utf-8")).hexdigest()[:24],
        text=body,
        post_url="",  # permalinks are per-card hrefs, attached by the caller
        author_name=author_name,
        author_headline=headline,
        author_profile_url="",  # resolved from the card's profile link by the caller
        posted_at=posted_at,
        raw={},
    )


@dataclass
class ScrollDecision:
    """One scroll-round verdict for the bounded scroll loop."""

    stop: bool
    reason: str
    exhausted: bool = False  # feed end reached (no more content exists)


def scroll_decision(
    *,
    rounds_without_growth: int,
    total_cards: int,
    stable_checks: int,
    max_posts: int,
    fuse: int = 80,
) -> ScrollDecision:
    """Virtualization-aware stop decision (design §6).

    LinkedIn unloads off-screen cards from the DOM, so a bare count can
    plateau while content still exists — or even drop mid-scroll. The
    stop signal is therefore TWO consecutive card-count checks with no
    growth (virtualization tolerance), a per-keyword fuse as the circuit
    breaker, and max_posts as the target cap when it's lower."""
    if total_cards >= max_posts:
        return ScrollDecision(True, f"cap {max_posts} reached")
    if total_cards >= fuse:
        return ScrollDecision(True, f"fuse {fuse} hit", exhausted=True)
    if rounds_without_growth >= stable_checks:
        return ScrollDecision(
            True,
            f"feed exhausted after {stable_checks} stable checks",
            exhausted=True,
        )
    return ScrollDecision(False, "keep scrolling")


def classify_browser_failure(detail: str) -> str:
    """Map a browser/search failure to the health status vocabulary
    (valid|degraded|expired|unavailable). Auth-shaped errors mean the
    session cookie is dead — 'expired', never 'unavailable'."""
    lowered = (detail or "").lower()
    markers = (
        "login",
        "log in",
        "sign in",
        "signin",
        "session",
        "auth",
        "checkpoint",
        "captcha",
        "cookie",
    )
    if any(m in lowered for m in markers):
        return "expired"
    return "unavailable"


# ---------- browser fingerprint mitigations (login-time) ----------
# A fresh Chromium under Playwright announces itself: navigator.webdriver
# reads True and the automation infobar shows — exactly what a risk system
# baits on for a new account, producing the captcha loop. These args strip
# the loudest tells, and launching the real installed Chrome/Edge (channel
# candidates, tried in order) swaps the bundled Chromium's fingerprint for
# a mainstream one. This is not evasion of a checkpoint a human must
# solve — it stops the browser looking like a bot BEFORE the human does
# their part, so solving the captcha once actually sticks.
STEALTH_ARGS = (
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
)
CHANNEL_CANDIDATES: tuple[str | None, ...] = ("chrome", "msedge", None)
