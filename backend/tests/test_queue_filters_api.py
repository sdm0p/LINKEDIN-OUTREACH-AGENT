"""API-level tests for the review-queue filters.

These go through the router -> service -> SQL chain, because the country
filter's correctness is a cross-layer property: the dropdown's value has to
survive the HTTP query string, the service signature, and the WHERE clause.

Also covers the backfill that resolves countries for leads ingested before
the location feature (dedup means a rerun never re-reads those posts, so
without a backfill the filter would sit empty on an existing queue).
"""

from fastapi.testclient import TestClient

from app.db import connect, get_db_path, init_db, insert_draft, utcnow
from app.main import app
from app.services import queue_service, resume_service


def _client():
    return TestClient(app)


def _row(post_id: str, text: str, country: str | None = None, posted_at: str | None = None):
    return {
        "post_id": post_id,
        "run_id": "r1",
        "keyword": "kw",
        "company": None,
        "role": None,
        "author_name": None,
        "author_headline": None,
        "author_profile_url": None,
        "post_url": None,
        "contact_method": "dm",
        "contact_value": None,
        "yoe_required": 0,
        "classification": "hiring",
        "location": None,
        "country": country,
        "posted_at": posted_at,
        "job_id": None,
        "job_url": None,
        "job_details_json": None,
        "post_text": text,
        "draft_text": None,
        "drafted_at": None,
        "status": "pending",
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }


def _seed(conn, *rows):
    for r in rows:
        insert_draft(conn, r)


def test_api_country_filter_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    async def _seed_rows():
        async with queue_service._db_lock:
            resume_service._ensure_db()
            conn = connect(get_db_path())
            try:
                _seed(
                    conn,
                    _row("in1", "Hiring!\n📍 Location: Bangalore, India. DM me."),
                    _row("de1", "We are hiring engineers in Germany. DM me."),
                    _row("unk", "We are hiring a Backend Engineer. DM me!"),
                )
            finally:
                conn.close()
    import asyncio
    asyncio.run(_seed_rows())

    with _client() as client:
        # The queue load itself performs the backfill, then filters.
        res = client.get("/api/queue", params={"country": "India"})
        assert res.status_code == 200
        rows = res.json()
        assert [r["post_id"] for r in rows] == ["in1"]
        assert rows[0]["country"] == "India"

        res = client.get("/api/queue", params={"country": "Germany"})
        assert [r["post_id"] for r in res.json()] == ["de1"]

        # The unknown bucket comes back through the same endpoint.
        res = client.get("/api/queue", params={"country": ""})
        assert [r["post_id"] for r in res.json()] == ["unk"]

        res = client.get("/api/queue")
        assert len(res.json()) == 3


def test_api_posted_order_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)

    async def _seed_rows():
        async with queue_service._db_lock:
            resume_service._ensure_db()
            conn = connect(get_db_path())
            try:
                _seed(
                    conn,
                    _row("old", "post", posted_at="2026-09-01T10:00:00+00:00"),
                    _row("new", "post", posted_at="2026-09-10T10:00:00+00:00"),
                    _row("nodate", "post", posted_at=None),
                )
            finally:
                conn.close()
    import asyncio
    asyncio.run(_seed_rows())

    with _client() as client:
        res = client.get("/api/queue", params={"order": "posted"})
        assert [r["post_id"] for r in res.json()] == ["new", "old", "nodate"]

        res = client.get("/api/queue", params={"order": "added"})
        assert [r["post_id"] for r in res.json()] == ["nodate", "new", "old"]


def test_api_countries_endpoint_after_backfill(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)

    async def _seed_rows():
        async with queue_service._db_lock:
            resume_service._ensure_db()
            conn = connect(get_db_path())
            try:
                _seed(
                    conn,
                    _row("in1", "Hiring engineers in India, apply now."),
                    _row("in2", "More hiring in Bengaluru!"),
                    _row("unk", "Hiring, DM me."),
                )
            finally:
                conn.close()
    import asyncio
    asyncio.run(_seed_rows())

    with _client() as client:
        # Loading the queue triggers the backfill; the dropdown endpoint
        # then reports what the queue actually holds.
        client.get("/api/queue")
        res = client.get("/api/queue/countries")
        assert res.status_code == 200
        assert res.json()["countries"] == ["India"]


def test_api_rejects_bad_order(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    with _client() as client:
        res = client.get("/api/queue", params={"order": "bogus"})
        assert res.status_code == 400


def test_backfill_is_idempotent_and_preserves_existing_countries(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)

    async def _seed_rows():
        async with queue_service._db_lock:
            resume_service._ensure_db()
            conn = connect(get_db_path())
            try:
                _seed(
                    conn,
                    _row("set", "Hiring in Germany.", country="Germany"),
                    _row("unset", "Hiring in Germany, DM me."),
                )
            finally:
                conn.close()
    import asyncio
    asyncio.run(_seed_rows())

    # Run twice; the second pass finds nothing left to do.
    result = asyncio.run(queue_service.backfill_missing_locations())
    second = asyncio.run(queue_service.backfill_missing_locations())
    assert second["updated"] == 0

    async def _read():
        async with queue_service._db_lock:
            conn = connect(get_db_path())
            try:
                return [dict(r) for r in conn.execute("SELECT * FROM drafts ORDER BY id")]
            finally:
                conn.close()
    rows = asyncio.run(_read())
    by_pid = {r["post_id"]: r for r in rows}
    assert by_pid["set"]["country"] == "Germany"  # untouched
    assert by_pid["unset"]["country"] == "Germany"  # backfilled once
    # The first call updated exactly the one resolvable row.
    assert result["updated"] == 1
