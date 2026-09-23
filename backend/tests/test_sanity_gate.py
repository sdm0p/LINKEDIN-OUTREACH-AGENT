"""Tests for the parse-sanity gate (branch item 2, design §7).

The contract: a thin or shapeless extraction must (a) never be marked
processed — dedup is forever — and (b) be skipped loudly, so the next
run retries it fresh. Normal posts must pass untouched.
"""

import sqlite3

import pytest

from app.pipeline import sanity
from app.search.base import Post
from app.services import queue_service


def _post(text="We are hiring a Backend Engineer for our platform team. DM me.", pid="p1", **kw):
    return Post(
        post_id=pid,
        text=text,
        author_name=kw.get("author_name", "Recruiter Rita"),
        author_headline=kw.get("author_headline", "Talent Lead at Acme"),
        author_profile_url="https://www.linkedin.com/in/rita/",
    )


def _seen_ids() -> set[str]:
    from app.config import settings
    from app.db import connect, seen_post_ids

    conn = connect(settings.data_dir / "app.db")
    try:
        return seen_post_ids(conn, [])
    finally:
        conn.close()


def _all_seen() -> set[str]:
    from app.config import settings

    conn = sqlite3.connect(settings.data_dir / "app.db")
    try:
        return {row[0] for row in conn.execute("SELECT post_id FROM processed_posts")}
    finally:
        conn.close()


class FakeProvider:
    name = "fake"
    calls = 0

    async def generate_json(self, *, system: str, user: str):
        self.calls += 1
        return {"verdict": "hiring", "company": "Acme", "role": "Dev", "emails": []}


class _Trace:
    def __init__(self):
        self.entries = []

    def log(self, stage, detail):
        self.entries.append((stage, detail))


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    return tmp_path


# ---------- unit: the gate itself ----------


def test_normal_post_passes():
    report = sanity.check_post(_post())
    assert report.ok
    assert report.reasons == []


def test_thin_text_fails():
    report = sanity.check_post(_post(text="hiring", pid="x" * 24))
    assert not report.ok
    assert any("20 chars" in r for r in report.reasons)


def test_empty_id_fails():
    report = sanity.check_post(_post(pid=""))
    assert not report.ok
    assert any("post_id" in r for r in report.reasons)


def test_short_but_present_id_passes():
    """A short id alone is not evidence of a broken extraction — the
    uniformity canary watches that pattern at the run level instead."""
    assert sanity.check_post(_post(pid="p1")).ok


def test_missing_identity_fails():
    report = sanity.check_post(_post(author_name="", author_headline=""))
    assert not report.ok
    assert any("author" in r for r in report.reasons)


def test_reasons_carry_all_failures():
    report = sanity.check_post(Post(post_id="", text="", author_name=""))
    assert len(report.reasons) >= 3


# ---------- integration: the ingest hook ----------


@pytest.mark.asyncio
async def test_thin_post_not_marked_processed(isolated, monkeypatch):
    """The core guarantee: gate-failed posts never reach dedup, so the
    next run sees them again fresh."""
    provider = FakeProvider()
    monkeypatch.setattr(queue_service, "get_llm_provider", lambda: provider)

    trace = _Trace()
    counts = await queue_service.ingest_posts(
        [_post(text="hiring", pid="thin-post-0001")], "kw", "run1", trace
    )
    assert counts["gate_dropped"] == 1
    assert counts["leads"] == 0
    assert provider.calls == 0  # never classified
    assert _all_seen() == set()  # NEVER marked processed
    assert any(stage == "sanity-gate" for stage, _ in trace.entries)


@pytest.mark.asyncio
async def test_gate_passes_normal_posts_through(isolated, monkeypatch):
    provider = FakeProvider()

    async def _people(query, trace=None):
        return [{"profile_url": "https://www.linkedin.com/in/rita/"}]

    monkeypatch.setattr(queue_service, "get_llm_provider", lambda: provider)
    monkeypatch.setattr(queue_service, "_search_people_safe", _people)

    counts = await queue_service.ingest_posts([_post()], "kw", "run1", _Trace())
    assert counts["gate_dropped"] == 0
    assert counts["leads"] == 1


@pytest.mark.asyncio
async def test_gate_runs_before_dedup(isolated, monkeypatch):
    """Ordering proof: an already-seen post is dedup-counted, the thin one
    gate-counted, and the thin one still absent from processed_posts."""
    provider = FakeProvider()
    monkeypatch.setattr(queue_service, "get_llm_provider", lambda: provider)

    good = _post(pid="good-post-0001")
    # First ingest marks the good post as processed.
    async def _people(query, trace=None):
        return [{"profile_url": "https://www.linkedin.com/in/rita/"}]
    monkeypatch.setattr(queue_service, "_search_people_safe", _people)
    await queue_service.ingest_posts([good], "kw", "run1", _Trace())

    monkeypatch.setattr(queue_service, "_search_people_safe", _people)
    trace = _Trace()
    counts = await queue_service.ingest_posts(
        [good, _post(text="hiring", pid="thin-post-0002")], "kw", "run2", trace
    )
    assert counts["dedup_skipped"] == 1
    assert counts["gate_dropped"] == 1
    assert "thin-post-0002" not in _all_seen()


@pytest.mark.asyncio
async def test_gate_failure_is_retried_next_run(isolated, monkeypatch):
    """The DLQ-before-DLQ behavior: a post that failed the gate once is
    classifiable on a later run, because it was never marked seen."""
    provider = FakeProvider()

    async def _people(query, trace=None):
        return [{"profile_url": "https://www.linkedin.com/in/rita/"}]

    monkeypatch.setattr(queue_service, "get_llm_provider", lambda: provider)
    monkeypatch.setattr(queue_service, "_search_people_safe", _people)

    thin = Post(post_id="retry-post-0001", text="hiring",
                author_name="Rita", author_headline="Talent Lead")
    await queue_service.ingest_posts([thin], "kw", "run1", _Trace())

    fixed = Post(post_id="retry-post-0001", text=thin.text + " — now fully extracted",
                 author_name="Rita", author_headline="Talent Lead")
    counts = await queue_service.ingest_posts([fixed], "kw", "run2", _Trace())
    assert counts["leads"] == 1  # recovered, not poisoned
