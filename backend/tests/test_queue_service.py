"""Ingest + on-demand draft tests with a scripted fake LLM — no network,
no real search.

The run pipeline only QUALIFIES posts into pending leads (dedup -> YoE ->
classification -> queue). Drafts are generated exclusively through the
draft graph's interrupt gate, exercised here end-to-end.
"""

import pytest

from app.pipeline.extraction import CLASSIFY_SYSTEM_PROMPT
from app.search.base import Post
from app.services import queue_service


class FakeProvider:
    name = "fake"

    def __init__(self, responses):
        self._responses = responses
        self.calls = 0
        self.draft_calls = 0

    async def generate_json(self, *, system: str, user: str):
        self.calls += 1
        if system == CLASSIFY_SYSTEM_PROMPT:
            return self._responses["classify"]
        self.draft_calls += 1
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
async def test_ingest_queues_pending_lead_no_draft(isolated, monkeypatch):
    """The core guarantee: a run never drafts. Kept posts land as pending
    leads with no draft text; only an explicit request may draft."""
    provider = FakeProvider(
        {
            "classify": {"verdict": "hiring", "company": "Acme", "role": "Backend Engineer", "emails": []},
            "draft": {"subject": "", "body": "SHOULD NEVER BE CALLED"},
        }
    )
    monkeypatch.setattr(queue_service, "get_llm_provider", lambda: provider)
    monkeypatch.setattr(queue_service, "_search_people_safe", _fake_people)

    counts = await queue_service.ingest_posts([_post()], "kw", "run1", _Trace())
    assert counts["leads"] == 1
    assert counts["errors"] == 0
    assert provider.draft_calls == 0  # draft stage never invoked

    rows = await queue_service.list_queue()
    assert len(rows) == 1
    lead = rows[0]
    assert lead["status"] == "pending"
    assert lead["draft_text"] is None
    assert lead["contact_method"] == "dm"
    assert lead["contact_value"] == "https://www.linkedin.com/in/rita/"
    assert "hiring a Backend Engineer" in lead["post_text"]


@pytest.mark.asyncio
async def test_generate_lead_draft_through_interrupt_gate(isolated, monkeypatch):
    """Explicit request -> draft graph -> interrupt gate -> resume -> draft
    stored, row flips pending -> new."""
    provider = FakeProvider(
        {
            "classify": {"verdict": "hiring", "company": "Acme", "role": "Backend Engineer", "emails": []},
            "draft": {"subject": "Backend Engineer", "body": "Hi Rita, I saw your post."},
        }
    )
    monkeypatch.setattr(queue_service, "get_llm_provider", lambda: provider)
    monkeypatch.setattr(queue_service, "_search_people_safe", _fake_people)

    await queue_service.ingest_posts([_post()], "kw", "run1", _Trace())
    pending = await queue_service.list_pending()
    assert len(pending) == 1

    counts = await queue_service.generate_lead_draft(pending[0]["id"])
    assert counts["draft_id"] == pending[0]["id"]
    assert provider.draft_calls == 1

    rows = await queue_service.list_queue()
    lead = rows[0]
    assert lead["status"] == "new"
    assert lead["draft_text"] == "Hi Rita, I saw your post."
    assert lead["drafted_at"] is not None


@pytest.mark.asyncio
async def test_generate_is_idempotent_guard(isolated, monkeypatch):
    """Drafting an already-drafted lead is rejected; the stored text is
    not overwritten."""
    provider = FakeProvider(
        {
            "classify": {"verdict": "hiring", "company": "Acme", "role": "Backend Engineer", "emails": []},
            "draft": {"subject": "", "body": "First draft"},
        }
    )
    monkeypatch.setattr(queue_service, "get_llm_provider", lambda: provider)
    monkeypatch.setattr(queue_service, "_search_people_safe", _fake_people)

    await queue_service.ingest_posts([_post()], "kw", "run1", _Trace())
    pending = await queue_service.list_pending()
    await queue_service.generate_lead_draft(pending[0]["id"])

    with pytest.raises(ValueError, match="already has a draft"):
        await queue_service.generate_lead_draft(pending[0]["id"])


@pytest.mark.asyncio
async def test_daily_cap_blocks_draft_generation(isolated, monkeypatch):
    """The cap applies at DRAFT time, not at ingest: browsing/qualifying
    is always allowed; generation past the cap is refused."""
    from app.config import settings

    monkeypatch.setattr(settings, "max_drafts_per_day", 0)
    provider = FakeProvider(
        {
            "classify": {"verdict": "hiring", "company": "Acme", "role": "Backend Engineer", "emails": []},
            "draft": {"subject": "", "body": "SHOULD NEVER BE CALLED"},
        }
    )
    monkeypatch.setattr(queue_service, "get_llm_provider", lambda: provider)
    monkeypatch.setattr(queue_service, "_search_people_safe", _fake_people)

    await queue_service.ingest_posts([_post(pid="cap1")], "kw", "run1", _Trace())
    pending = await queue_service.list_pending()

    with pytest.raises(ValueError, match="cap"):
        await queue_service.generate_lead_draft(pending[0]["id"])
    assert provider.draft_calls == 0

    # The lead is untouched and still pending for tomorrow.
    rows = await queue_service.list_queue()
    assert rows[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_generate_all_respects_cap_and_counts(isolated, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "max_drafts_per_day", 1)
    provider = FakeProvider(
        {
            "classify": {"verdict": "hiring", "company": "Acme", "role": "Backend Engineer", "emails": []},
            "draft": {"subject": "", "body": "Hello"},
        }
    )
    monkeypatch.setattr(queue_service, "get_llm_provider", lambda: provider)
    monkeypatch.setattr(queue_service, "_search_people_safe", _fake_people)

    await queue_service.ingest_posts(
        [_post(pid="g1"), _post(pid="g2")], "kw", "run1", _Trace()
    )
    result = await queue_service.generate_all_pending()
    assert result["generated"] == 1
    assert result["capped"] == 1

    rows = await queue_service.list_queue()
    statuses = sorted(r["status"] for r in rows)
    assert statuses == ["new", "pending"]


@pytest.mark.asyncio
async def test_email_lead_stores_post_text_and_contact(isolated, monkeypatch):
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
    rows = await queue_service.list_queue()
    lead = rows[0]
    assert lead["status"] == "pending"
    assert lead["contact_method"] == "email"
    assert lead["contact_value"] == "john.doe@gmail.com"


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
    assert counts["leads"] == 0
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
    assert counts["leads"] == 0
    assert len(await queue_service.list_queue()) == 1


async def _fake_people(query, trace=None):
    return [{"name": "Recruiter Rita", "profile_url": "https://www.linkedin.com/in/rita/"}]
