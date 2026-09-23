"""Tests for the dead-letter queue (branch item 3, design §7).

Contract: failed captures park with their raw payload; nothing in the
DLQ is ever marked processed; run-start replay recovers entries whose
parse now succeeds; exhausted attempts park visibly; the API list/
replay/purge endpoints behave.
"""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.llm import LLMError
from app.main import app
from app.search.base import Post
from app.services import queue_service


class FakeProvider:
    name = "fake"

    def __init__(self, responses):
        self._responses = responses
        self.calls = 0

    async def generate_json(self, *, system: str, user: str):
        self.calls += 1
        return self._responses


class BoomProvider:
    name = "boom"

    async def generate_json(self, *, system: str, user: str):
        raise LLMError("429 quota exceeded")


class _Trace:
    def __init__(self):
        self.entries = []

    def log(self, stage, detail):
        self.entries.append((stage, detail))


def _post(pid="p1", text="We are hiring a Backend Engineer for our platform team. DM me."):
    return Post(
        post_id=pid,
        text=text,
        author_name="Recruiter Rita",
        author_headline="Talent Lead at Acme",
        author_profile_url="https://www.linkedin.com/in/rita/",
    )


@pytest.fixture()
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    return tmp_path


async def _dlq_rows() -> list[dict]:
    from app.db import connect, dlq_list, get_db_path

    async with queue_service._db_lock:
        conn = connect(get_db_path())
        try:
            return [dict(r) for r in dlq_list(conn)]
        finally:
            conn.close()


async def _overwrite_payload(identity: str, payload: dict) -> None:
    """Simulate a parser fix by rewriting a DLQ row's stored payload."""
    from app.db import connect, dlq_add, get_db_path

    keyword = identity.rsplit("|", 1)[1]

    async with queue_service._db_lock:
        conn = connect(get_db_path())
        try:
            dlq_add(conn, {
                "run_id": "run1", "keyword": keyword,
                "stage": "sanity-gate", "failure_reason": "text under 20 chars",
                "author_name": payload.get("author_name"),
                "post_url": payload.get("post_url") or "",
                "identity": identity,
                "raw_json": json.dumps(payload),
                "last_attempt_at": "2026-01-01T00:00:00+00:00",
                "created_at": "2026-01-01T00:00:00+00:00",
            })
        finally:
            conn.close()


async def _people_ok(query, trace=None):
    return [{"profile_url": "https://www.linkedin.com/in/rita/"}]


# ---------- capture ----------


@pytest.mark.asyncio
async def test_gate_failure_parks_entry_with_payload(isolated, monkeypatch):
    provider = FakeProvider({"verdict": "hiring"})
    monkeypatch.setattr(queue_service, "get_llm_provider", lambda: provider)

    thin = Post(post_id="thin-post-0001", text="hiring",
                author_name="Rita", author_headline="Talent Lead")
    await queue_service.ingest_posts([thin], "kw", "run1", _Trace())

    rows = await _dlq_rows()
    assert len(rows) == 1
    assert rows[0]["stage"] == "sanity-gate"
    assert "20 chars" in rows[0]["failure_reason"]
    assert rows[0]["attempts"] == 1
    assert rows[0]["keyword"] == "kw"
    payload = json.loads(rows[0]["raw_json"])
    assert payload["post_id"] == "thin-post-0001"


@pytest.mark.asyncio
async def test_llm_failure_parks_entry(isolated, monkeypatch):
    monkeypatch.setattr(queue_service, "get_llm_provider", lambda: BoomProvider())

    await queue_service.ingest_posts([_post("llm-1")], "kw", "run1", _Trace())
    rows = await _dlq_rows()
    assert len(rows) == 1
    assert rows[0]["stage"] == "llm"
    assert "429" in rows[0]["failure_reason"]
    assert rows[0]["attempts"] == 1


@pytest.mark.asyncio
async def test_same_post_twice_bumps_attempts(isolated, monkeypatch):
    monkeypatch.setattr(queue_service, "get_llm_provider", lambda: BoomProvider())

    await queue_service.ingest_posts([_post("dup-1")], "kw", "run1", _Trace())
    await queue_service.ingest_posts([_post("dup-1")], "kw", "run2", _Trace())
    rows = await _dlq_rows()
    assert len(rows) == 1
    assert rows[0]["attempts"] == 2


# ---------- replay ----------


@pytest.mark.asyncio
async def test_replay_recovers_gate_failure_after_fix(isolated, monkeypatch):
    """The headline scenario: a thin extraction parks; the parser 'gets
    fixed' (the stored payload is rewritten with full text); run-start
    replay turns it into a lead and clears the entry."""
    provider = FakeProvider({"verdict": "hiring"})
    monkeypatch.setattr(queue_service, "get_llm_provider", lambda: provider)
    monkeypatch.setattr(queue_service, "_search_people_safe", _people_ok)

    thin = Post(post_id="fix-me-0001", text="hiring",
                author_name="Rita", author_headline="Talent Lead")
    await queue_service.ingest_posts([thin], "kw", "run1", _Trace())
    assert len(await _dlq_rows()) == 1

    # Replay without a fix: gate fails again, entry stays.
    summary = await queue_service.replay_dlq(_Trace())
    assert summary["replayed"] == 1
    assert summary["recovered"] == 0
    assert len(await _dlq_rows()) == 1

    # The parser is fixed: corrected payload stored in the entry.
    await _overwrite_payload("fix-me-0001|kw", {
        "post_id": "fix-me-0001",
        "text": "We are hiring a Backend Engineer for our platform team. DM me.",
        "post_url": "", "author_name": "Rita", "author_headline": "Talent Lead",
        "author_profile_url": "", "posted_at": "", "raw": {},
    })

    summary = await queue_service.replay_dlq(_Trace())
    assert summary["recovered"] == 1
    assert await _dlq_rows() == []
    leads = await queue_service.list_queue()
    assert len(leads) == 1
    assert leads[0]["post_id"] == "fix-me-0001"


@pytest.mark.asyncio
async def test_replay_never_marks_processed_on_failure(isolated, monkeypatch):
    monkeypatch.setattr(queue_service, "get_llm_provider", lambda: FakeProvider({"verdict": "hiring"}))

    thin = Post(post_id="still-thin-1", text="hiring",
                author_name="Rita", author_headline="Talent Lead")
    await queue_service.ingest_posts([thin], "kw", "run1", _Trace())
    await queue_service.replay_dlq(_Trace())

    from app.config import settings

    conn = sqlite3.connect(settings.data_dir / "app.db")
    try:
        seen = conn.execute("SELECT COUNT(*) FROM processed_posts").fetchone()[0]
    finally:
        conn.close()
    assert seen == 0  # replay failed -> still not marked processed


@pytest.mark.asyncio
async def test_replay_bumps_attempts_until_parked(isolated, monkeypatch):
    monkeypatch.setattr(queue_service, "get_llm_provider", lambda: BoomProvider())

    from app.db import dlq_max_attempts

    await queue_service.ingest_posts([_post("boom-1")], "kw", "run1", _Trace())
    for _ in range(dlq_max_attempts() - 1):
        summary = await queue_service.replay_dlq(_Trace())
        assert summary["still_failing"] == 1
    rows = await _dlq_rows()
    assert rows[0]["attempts"] == dlq_max_attempts()

    # Exhausted: replay skips it entirely.
    summary = await queue_service.replay_dlq(_Trace())
    assert summary["replayed"] == 0


@pytest.mark.asyncio
async def test_replay_clears_entry_when_post_becomes_noise(isolated, monkeypatch):
    """Replay success isn't only 'became a lead' — a post that now
    classifies as noise is resolved too (the DLQ is a retry queue, not
    a lead factory)."""
    provider = FakeProvider({"verdict": "noise", "company": "", "role": "", "emails": []})
    monkeypatch.setattr(queue_service, "get_llm_provider", lambda: provider)

    thin = Post(post_id="noise-1", text="hiring",
                author_name="Rita", author_headline="Talent Lead")
    await queue_service.ingest_posts([thin], "kw", "run1", _Trace())
    assert len(await _dlq_rows()) == 1

    await _overwrite_payload("noise-1|kw", {
        "post_id": "noise-1",
        "text": "Exciting updates coming soon to our platform. Stay tuned everyone!",
        "post_url": "", "author_name": "Rita", "author_headline": "Talent Lead",
        "author_profile_url": "", "posted_at": "", "raw": {},
    })

    summary = await queue_service.replay_dlq(_Trace())
    assert summary["recovered"] == 1
    assert await _dlq_rows() == []


# ---------- API ----------


@pytest.mark.asyncio
async def test_dlq_api_list_replay_purge(isolated, monkeypatch):
    monkeypatch.setattr(queue_service, "get_llm_provider", lambda: FakeProvider({"verdict": "hiring"}))

    thin = Post(post_id="api-thin-001", text="hiring",
                author_name="Rita", author_headline="Talent Lead")
    await queue_service.ingest_posts([thin], "kw", "run1", _Trace())

    with TestClient(app) as client:
        res = client.get("/api/settings/dlq")
        assert res.status_code == 200
        body = res.json()
        assert body["max_attempts"] == 5
        assert body["row_cap"] == 500
        assert len(body["entries"]) == 1
        assert body["entries"][0]["stage"] == "sanity-gate"

        res = client.post("/api/settings/dlq/replay")
        assert res.status_code == 200
        assert res.json()["replayed"] == 1

        res = client.post("/api/settings/dlq/purge", json={"exhausted_only": False})
        assert res.status_code == 200

        res = client.get("/api/settings/dlq")
        assert res.json()["entries"] == []


def test_dlq_api_empty_state(isolated):
    with TestClient(app) as client:
        res = client.get("/api/settings/dlq")
        assert res.status_code == 200
        body = res.json()
        assert body["entries"] == []
        assert body["parked_count"] == 0
