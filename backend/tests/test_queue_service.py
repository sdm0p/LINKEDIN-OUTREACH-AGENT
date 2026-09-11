"""Ingest-flow tests with a scripted fake LLM — no network, no real search."""

import pytest

from app.pipeline.extraction import CLASSIFY_SYSTEM_PROMPT
from app.search.base import Post
from app.services import queue_service


class FakeProvider:
    name = "fake"

    def __init__(self, responses):
        self._responses = responses
        self.calls = 0

    async def generate_json(self, *, system: str, user: str):
        self.calls += 1
        if system == CLASSIFY_SYSTEM_PROMPT:
            return self._responses["classify"]
        return self._responses["draft"]


def _post(text="We are hiring a Backend Engineer for our platform team. DM me.", pid="p1"):
    return Post(
        post_id=pid,
        text=text,
        author_name="Recruiter Rita",
        author_headline="Talent Lead at Acme",
        author_profile_url="https://www.linkedin.com/in/rita/",
    )


class _Trace:
    def __init__(self):
        self.entries = []

    def log(self, stage, detail):
        self.entries.append((stage, detail))


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    return tmp_path


@pytest.mark.asyncio
async def test_ingest_creates_dm_draft(isolated, monkeypatch):
    provider = FakeProvider(
        {
            "classify": {"verdict": "hiring", "company": "Acme", "role": "Backend Engineer", "emails": []},
            "draft": {"subject": "", "body": "Hi Rita, I saw your post about the Backend Engineer role."},
        }
    )
    monkeypatch.setattr(queue_service, "get_llm_provider", lambda: provider)
    monkeypatch.setattr(queue_service, "_search_people_safe", _fake_people)

    counts = await queue_service.ingest_posts([_post()], "kw", "run1", _Trace())
    assert counts["drafts"] == 1
    drafts = await queue_service.list_queue()
    assert drafts[0]["contact_method"] == "dm"
    assert drafts[0]["contact_value"] == "https://www.linkedin.com/in/rita/"
    assert drafts[0]["status"] == "new"


@pytest.mark.asyncio
async def test_ingest_creates_email_draft_when_email_found(isolated, monkeypatch):
    provider = FakeProvider(
        {
            "classify": {
                "verdict": "hiring",
                "company": "Acme",
                "role": "Backend Engineer",
                "emails": [],
            },
            "draft": {"subject": "Backend Engineer", "body": "Hello,"},
        }
    )
    monkeypatch.setattr(queue_service, "get_llm_provider", lambda: provider)
    text = "We are hiring. Email john.doe@gmail.com with your CV."
    monkeypatch.setattr(queue_service, "_search_people_safe", _fake_people)

    await queue_service.ingest_posts([_post(text=text, pid="p2")], "kw", "run1", _Trace())
    drafts = await queue_service.list_queue()
    assert drafts[0]["contact_method"] == "email"
    assert drafts[0]["contact_value"] == "john.doe@gmail.com"
    assert drafts[0]["draft_text"].startswith("Subject:")


@pytest.mark.asyncio
async def test_noise_dropped(isolated, monkeypatch):
    provider = FakeProvider(
        {
            "classify": {"verdict": "noise", "company": "", "role": "", "emails": []},
            "draft": {"subject": "", "body": "unused"},
        }
    )
    monkeypatch.setattr(queue_service, "get_llm_provider", lambda: provider)

    counts = await queue_service.ingest_posts([_post(pid="p3")], "kw", "run1", _Trace())
    assert counts["noise"] == 1
    assert counts["drafts"] == 0
    assert await queue_service.list_queue() == []


@pytest.mark.asyncio
async def test_yoe_drop_before_llm(isolated, monkeypatch):
    provider = FakeProvider(
        {
            "classify": {"verdict": "hiring", "company": "", "role": "", "emails": []},
            "draft": {"subject": "", "body": "unused"},
        }
    )
    monkeypatch.setattr(queue_service, "get_llm_provider", lambda: provider)

    counts = await queue_service.ingest_posts(
        [_post(text="Need 9+ years architect. DM me.", pid="p4")], "kw", "run1", _Trace()
    )
    assert counts["yoe_dropped"] == 1
    assert provider.calls == 0  # never reached the LLM
    assert await queue_service.list_queue() == []


@pytest.mark.asyncio
async def test_dedup_skips_already_processed(isolated, monkeypatch):
    provider = FakeProvider(
        {
            "classify": {"verdict": "hiring", "company": "Acme", "role": "Backend", "emails": []},
            "draft": {"subject": "", "body": "Hi again"},
        }
    )
    monkeypatch.setattr(queue_service, "get_llm_provider", lambda: provider)
    monkeypatch.setattr(queue_service, "_search_people_safe", _fake_people)

    await queue_service.ingest_posts([_post(pid="p5")], "kw", "run1", _Trace())
    counts = await queue_service.ingest_posts([_post(pid="p5")], "kw", "run2", _Trace())
    assert counts["dedup_skipped"] == 1
    assert counts["drafts"] == 0
    assert len(await queue_service.list_queue()) == 1


@pytest.mark.asyncio
async def test_daily_cap_stops_drafts(isolated, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "max_drafts_per_day", 1)
    provider = FakeProvider(
        {
            "classify": {"verdict": "hiring", "company": "Acme", "role": "Backend", "emails": []},
            "draft": {"subject": "", "body": "Hi"},
        }
    )
    monkeypatch.setattr(queue_service, "get_llm_provider", lambda: provider)
    monkeypatch.setattr(queue_service, "_search_people_safe", _fake_people)

    counts = await queue_service.ingest_posts(
        [_post(pid="c1"), _post(pid="c2")], "kw", "run1", _Trace()
    )
    assert counts["drafts"] == 1
    assert counts["capped"] == 1
    assert len(await queue_service.list_queue()) == 1


async def _fake_people(query, trace=None):
    return [{"name": "Recruiter Rita", "profile_url": "https://www.linkedin.com/in/rita/"}]
