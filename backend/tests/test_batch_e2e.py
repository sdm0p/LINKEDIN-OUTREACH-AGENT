"""Full-pipeline batch test — the "run it and check all features" harness.

A real run needs Docker + the LinkedIn session + a Gemini key, so this
drives the REAL code paths with one realistic scripted MCP response:

  real run graph (load_resume -> pick_keywords -> search_batch ->
  ingest_batch) -> real queue API (filters, status, drafts, backfill,
  fetch-job) -> real draft graph (interrupt gate) -> stored drafts.

Only two seams are faked: the MCP container (its response is the recorded
live shape: person/feed_post/job references + relative ages) and the LLM
(classify/draft/role-expansion responses, including a location field).

Batch of 4 posts and what must happen to each:
  1. Aravind  — Training Developer, "📍 Location: Bangalore, India",
                2+ Years, gmail in text, ATTACHED JOB CARD, 19h, /posts/
                permalink  -> lead (email contact), country India, job id
                captured, fetch-job hits that job directly.
  2. Sujal    — Java Developer, Remote (US), 0-5 Years, DM me, 3h,
                /feed/update/ permalink -> lead (DM fallback), country
                United States from the LLM's location field.
  3. Manoj    — .NET Developer, Berlin, minimum 9 YEARS -> dropped by the
                YoE filter before any LLM call.
  4. Rita     — "ranting about recruiters" -> classified noise.
"""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.pipeline.extraction import CLASSIFY_SYSTEM_PROMPT
from app.pipeline.role_expansion import EXPAND_SYSTEM_PROMPT
from app.search.base import Post, SearchResult


def _post_search_response() -> dict:
    """Shape mirrors the live search_posts response: one text blob;
    person/feed_post/job references in feed order."""
    return {
        "url": "https://www.linkedin.com/search/results/content/?keywords=hiring+developer",
        "sections": {
            "search_results": (
                "Feed post\n\n"
                "Aravind Kumar\n\n \n • 3rd+\n\nTechnical Recruiter at Gios Technology\n\n19h • \n\n"
                "Follow\n\n"
                "We are Hiring: Training Developer | Onsite\n"
                "📍 Location: Bangalore, India\n"
                "Experience: 2+ Years\n"
                "Email aravind.rg@gmail.com with your CV\n\n"
                "Training Development Specialist (Verified job)\n\nView job\n\n"
                "Actively reviewing applicants\n"
                "Feed post\n\n"
                "Sujal Vanpariya\n\n \n • 3rd+\n\nBusiness Development Executive at Krox\n\n3h • \n\n"
                "Follow\n\n"
                "Immediate Hiring | Java Developer | Remote (US)\n"
                "Experience:0–5 Years\nDM me\n\n"
                "42\n"
                "Feed post\n\n"
                "Manoj kumar Singaravel\n\n \n • 3rd+\n\nTeam Lead | Vee Technologies\n\n2d • \n\n"
                "Follow\n\n"
                "WE'RE HIRING | .NET DEVELOPER\n📍 Location: Berlin, Germany\n"
                "minimum 9 years required. DM me.\n\n"
                "7\n"
                "Feed post\n\n"
                "Reposter Rita\n\n \n • 2nd\n\nGrowth hacker\n\n1w • \n\n"
                "Follow\n\n"
                "Not hiring, just ranting about recruiters.\n\n"
                "3\n"
            )
        },
        "references": {
            "search_results": [
                {"kind": "person", "url": "/in/aravind-kumar-71b418201/", "text": "Aravind Kumar", "context": "search result"},
                {"kind": "job", "url": "/jobs/view/4464542122/", "text": "Training Development Specialist", "context": "job result"},
                {"kind": "feed_post", "url": "/posts/aravind-kumar_gios-activity-7200000000000000000-AbCd", "context": "search_results"},
                {"kind": "feed_post", "url": "/feed/update/urn:li:activity:7100000000000000000/", "context": "search_results"},
                {"kind": "feed_post", "url": "/posts/manoj-kumar_vee-ugcPost-7300000000000000000-EfGh", "context": "search_results"},
                {"kind": "feed_post", "url": "/posts/reposter-rita_share-7400000000000000000-IjKl", "context": "search_results"},
                {"kind": "person", "url": "/in/sujal-vanpariya-6273a5321/", "text": "Sujal Vanpariya", "context": "search result"},
                {"kind": "person", "url": "/in/manoj-kumar-931b/", "text": "Manoj kumar Singaravel", "context": "search result"},
                {"kind": "person", "url": "/in/reposter-rita/", "text": "Reposter Rita", "context": "search result"},
            ]
        },
    }


JOB_DETAILS_RESPONSE = {
    "url": "https://www.linkedin.com/jobs/view/4464542122/",
    "sections": {
        "job_posting": (
            "Training Development Specialist\nGIOS Technology\n"
            "📍 Location: Cumbria (On-site)\nActively reviewing applicants"
        )
    },
}


class FakeMCPSource:
    """Stands in for LinkedInMCPSource: same interface, scripted data."""

    name = "linkedin"

    def __init__(self):
        self.get_job_calls: list[str] = []
        self.search_job_calls: list[str] = []

    async def search_posts(self, keyword, recency):
        data = _post_search_response()
        from app.search.linkedin_mcp import (
            _job_references,
            _parse_post_chunk,
            _person_references,
            _post_permalinks,
            _split_feed_posts,
        )

        refs = _person_references(data)
        permalinks = _post_permalinks(data)
        job_refs = _job_references(data)
        posts: list[Post] = []
        for lines in _split_feed_posts(data["sections"]["search_results"]):
            post = _parse_post_chunk(lines, refs)
            if post is not None:
                posts.append(post)
        for i, post in enumerate(posts):
            if i < len(permalinks):
                post.post_url = permalinks[i]
            if i < len(job_refs):
                post.raw["job_id"] = job_refs[i]["job_id"]
                post.raw["job_url"] = job_refs[i]["job_url"]
        return SearchResult(posts=posts, raw_hit_count=len(posts))

    async def get_job_details(self, job_id):
        self.get_job_calls.append(job_id)
        return JOB_DETAILS_RESPONSE

    async def search_job_ids(self, keyword, location=None):
        self.search_job_calls.append(keyword)
        return [{"job_id": "999", "job_url": "https://www.linkedin.com/jobs/view/999/"}]

    async def search_people(self, query):
        return [{"name": "Sujal Vanpariya", "profile_url": "https://www.linkedin.com/in/sujal-vanpariya-6273a5321/"}]

    async def check_health(self):
        return {"status": "valid", "detail": "fake"}


class FakeLLM:
    name = "fake"

    def __init__(self):
        self.classify_calls = 0
        self.draft_calls = 0

    async def generate_json(self, *, system: str, user: str):
        if system == CLASSIFY_SYSTEM_PROMPT:
            self.classify_calls += 1
            # The classifier prompt carries headline + post text (not the
            # author name), so key scripted verdicts off that. Only 3 of
            # the 4 posts reach the LLM: the 9y post is YoE-dropped first.
            if "Training Developer" in user:
                return {"verdict": "hiring", "company": "Gios Technology", "role": "Training Developer", "emails": [], "location": "India"}
            if "Java Developer" in user:
                return {"verdict": "hiring", "company": "Krox", "role": "Java Developer", "emails": [], "location": "United States"}
            return {"verdict": "noise", "company": "", "role": "", "emails": [], "location": ""}
        if system == EXPAND_SYSTEM_PROMPT:
            return ["python developer", "backend engineer"]
        self.draft_calls += 1
        return {"subject": "Backend Engineer role", "body": "Hi, I saw your hiring post and would like to apply."}


@pytest.fixture()
def batch_env(tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.data_dir", tmp_path)
    # No geo targets by default — the dedicated location-filter tests set
    # and clear their own.
    from app.config import set_target_countries

    set_target_countries([])

    fake_source = FakeMCPSource()
    fake_llm = FakeLLM()

    import app.search.base as search_base

    monkeypatch.setattr(search_base, "get_linkedin_source", lambda: fake_source)

    from app.services import queue_service, resume_service

    monkeypatch.setattr(queue_service, "get_llm_provider", lambda: fake_llm)
    monkeypatch.setattr(resume_service, "get_llm_provider", lambda: fake_llm)

    # Seed a parsed resume (YoE 3: keeps the 2y and 0-5y posts, drops the
    # 9y one) and a keyword pool so the real run graph has inputs.
    from app.db import connect, get_db_path, init_db, insert_keyword, save_resume_cache
    from app.models import ParsedResume

    init_db(get_db_path())
    parsed = ParsedResume(
        name="Test Candidate",
        skills=["python", "sql"],
        roles=["Backend Engineer"],
        total_experience_years=3.0,
    )
    conn = connect(get_db_path())
    try:
        save_resume_cache(
            conn,
            {
                "file_hash": "h",
                "file_name": "resume.pdf",
                "file_size": 1000,
                "parsed_json": parsed.model_dump_json(),
                "my_yoe": 3,
                "roles_expanded_json": "[]",
                "parsed_at": "2026-09-18T00:00:00+00:00",
                "expanded_at": "2026-09-18T00:00:00+00:00",
            },
        )
        # One keyword = one batch, so the run graph's between-batch human
        # pacing pause (3-6s) never fires and the test stays fast.
        insert_keyword(conn, text="hiring developer", tier="skill", created_at="2026-09-18T00:00:00+00:00")
    finally:
        conn.close()

    return {"source": fake_source, "llm": fake_llm}


def _run_and_wait(client) -> str:
    res = client.post("/api/runs", json={"recency": "24h"})
    assert res.status_code == 200
    run_id = res.json()["run_id"]
    import time

    for _ in range(200):
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in ("completed", "failed"):
            return run_id
        time.sleep(0.05)
    raise AssertionError("run did not finish in time")


# ---------------------------------------------------------------- the batch


def test_batch_end_to_end_all_features(batch_env):
    source = batch_env["source"]
    llm = batch_env["llm"]

    with TestClient(app) as client:
        # 0) Resume state visible.
        state = client.get("/api/resume").json()
        assert state["has_resume"] is True
        assert state["my_yoe"] == 3

        # 1) Real run over the batch.
        run_id = _run_and_wait(client)
        run = client.get(f"/api/runs/{run_id}").json()
        assert run["status"] == "completed", run.get("error")
        summary = run["summary"]
        assert summary["raw_hits"] == 4
        assert summary["yoe_dropped"] == 1  # Manoj's 9y post
        assert summary["noise"] == 1  # Rita's rant
        assert summary["leads_queued"] == 2
        # The YoE drop happened before any LLM call: 3 posts classified
        # (Aravind, Sujal, Rita), not 4.
        assert llm.classify_calls == 3

        # 2) Queue shows exactly the two leads; nothing drafted in-run.
        queue = client.get("/api/queue").json()
        assert len(queue) == 2
        assert all(d["status"] == "pending" and d["draft_text"] is None for d in queue)
        by_company = {d["company"]: d for d in queue}
        aravind = by_company["Gios Technology"]
        sujal = by_company["Krox"]

        # --- permalink: the queue link is the POST, never the profile ---
        # Positional mapping: post 1 -> /posts/ permalink, post 2 ->
        # /feed/update/ permalink (both valid post permalink forms).
        assert aravind["post_url"].startswith("https://www.linkedin.com/posts/aravind-kumar_gios")
        assert sujal["post_url"].startswith("https://www.linkedin.com/feed/update/")
        assert aravind["post_url"] != aravind["author_profile_url"]
        assert sujal["post_url"] != sujal["author_profile_url"]

        # --- contact resolution: email in text vs DM fallback ---
        assert aravind["contact_method"] == "email"
        assert aravind["contact_value"] == "aravind.rg@gmail.com"
        assert sujal["contact_method"] == "dm"
        assert sujal["contact_value"].endswith("/in/sujal-vanpariya-6273a5321/")

        # --- posted time: parsed from the relative age; ordering works ---
        assert aravind["posted_at"] is not None  # "19h •"
        assert sujal["posted_at"] is not None  # "3h •"
        posted_order = client.get("/api/queue", params={"order": "posted"}).json()
        times = [d["posted_at"] for d in posted_order]
        assert times == sorted(times, reverse=True)
        assert posted_order[0]["post_id"] == sujal["post_id"]  # 3h newest first

        # --- country: LLM field + labeled-line fallback; filter works ---
        assert aravind["country"] == "India"
        assert aravind["location"] == "Bangalore, India"  # labeled line kept
        assert sujal["country"] == "United States"  # LLM location field
        res = client.get("/api/queue", params={"country": "India"})
        assert [d["id"] for d in res.json()] == [aravind["id"]]
        res = client.get("/api/queue", params={"country": "United States"})
        assert [d["id"] for d in res.json()] == [sujal["id"]]
        res = client.get("/api/queue", params={"country": "Germany"})
        assert res.json() == []  # Berlin post never became a lead (YoE)
        res = client.get("/api/queue", params={"country": ""})
        assert res.json() == []  # no unknown rows in this batch
        countries = client.get("/api/queue/countries").json()["countries"]
        assert countries == ["India", "United States"]

        # --- combined filters: status AND country ---
        res = client.get("/api/queue", params={"status": "pending", "country": "India"})
        assert [d["id"] for d in res.json()] == [aravind["id"]]

        # 3) Job enrichment: the attached job card was captured at ingest.
        assert aravind["job_id"] == "4464542122"
        assert aravind["job_url"] == "https://www.linkedin.com/jobs/view/4464542122/"
        assert sujal["job_id"] is None

        res = client.post(f"/api/queue/{aravind['id']}/fetch-job")
        assert res.status_code == 200, res.text
        fetched = res.json()
        # Direct fetch of the captured job — no keyword search fallback.
        assert source.get_job_calls == ["4464542122"]
        assert source.search_job_calls == []
        assert "Training Development Specialist" in fetched["text"]
        assert fetched["location"] == "Cumbria (On-site)"

        rows = {d["id"]: d for d in client.get("/api/queue").json()}
        assert "Training Development Specialist" in rows[aravind["id"]]["job_details_json"]

        # 4) Draft through the real interrupt-gated draft graph (pending
        # leads only — the gate refuses anything else).
        res = client.post(f"/api/queue/{sujal['id']}/generate")
        assert res.status_code == 200, res.text
        assert llm.draft_calls == 1
        rows = {d["id"]: d for d in client.get("/api/queue").json()}
        drafted = rows[sujal["id"]]
        assert drafted["status"] == "new"
        # DM drafts carry the body only; the Subject line is for emails.
        assert drafted["draft_text"] == "Hi, I saw your hiring post and would like to apply."
        assert drafted["drafted_at"] is not None

        # Re-drafting a drafted lead is refused.
        res = client.post(f"/api/queue/{sujal['id']}/generate")
        assert res.status_code == 400
        assert "already has a draft" in res.json()["detail"]

        # 5) Status transitions + status filter after drafting.
        res = client.patch(f"/api/queue/{sujal['id']}/status", json={"status": "reviewed"})
        assert res.status_code == 200
        res = client.get("/api/queue", params={"status": "reviewed"})
        assert [d["id"] for d in res.json()] == [sujal["id"]]

        # 6) Generate-all drafts the remaining pending lead.
        res = client.post("/api/queue/generate-all")
        assert res.status_code == 200
        body = res.json()
        assert body["generated"] == 1
        assert body["failed"] == 0
        assert body["capped"] == 0
        statuses = sorted(d["status"] for d in client.get("/api/queue").json())
        assert statuses == ["new", "reviewed"]
        # The email lead's draft carries the Subject line.
        rows = {d["id"]: d for d in client.get("/api/queue").json()}
        assert "Subject: Backend Engineer role" in rows[aravind["id"]]["draft_text"]

        # 7) Dedup: a rerun over the same posts queues nothing new.
        run2 = _run_and_wait(client)
        summary2 = client.get(f"/api/runs/{run2}").json()["summary"]
        assert summary2["dedup_skipped"] == 4
        assert summary2["leads_queued"] == 0
        assert len(client.get("/api/queue").json()) == 2

        # 8) Purge (manual only): clears rows + dedup entries.
        res = client.post("/api/queue/purge", json={})
        assert res.status_code == 200
        assert res.json()["removed"] >= 2
        assert client.get("/api/queue").json() == []
        # Dedup is cleared too, so a fresh run re-ingests.
        run3 = _run_and_wait(client)
        summary3 = client.get(f"/api/runs/{run3}").json()["summary"]
        assert summary3["leads_queued"] == 2


def test_batch_location_filter_drops_outside_targets(batch_env, monkeypatch):
    """Targets set -> the Berlin post is dropped by the location filter
    before the LLM (on top of the YoE drop), and the India post survives."""
    from app.config import set_target_countries

    set_target_countries(["India"])
    llm = batch_env["llm"]

    with TestClient(app) as client:
        run_id = _run_and_wait(client)
        run = client.get(f"/api/runs/{run_id}").json()
        assert run["status"] == "completed", run.get("error")
        summary = run["summary"]
        # Manoj (Berlin, 9y) is now dropped twice over: YoE fires first,
        # then the location filter never even sees him. Sujal's Remote (US)
        # post passes the pre-LLM pass (remote is not provably outside) and
        # gets caught by the post-LLM net via the classifier's location.
        assert summary["yoe_dropped"] == 1
        assert summary["location_dropped"] == 1  # Sujal, post-LLM (United States)
        assert summary["leads_queued"] == 1
        assert llm.classify_calls == 2  # Aravind + Sujal; Manoj/Rita never asked

        queue = client.get("/api/queue").json()
        assert [d["company"] for d in queue] == ["Gios Technology"]


def test_batch_geo_scopes_search_with_single_target(batch_env):
    """One target country -> the search query carries it (biasing results
    toward India-heavy posts); multiple/no targets leave queries alone."""
    from app.config import set_target_countries

    seen_queries = []

    class SpySource(batch_env["source"].__class__):
        async def search_posts(self, keyword, recency):
            seen_queries.append(keyword)
            return await super().search_posts(keyword, recency)

    import app.graphs.nodes as nodes

    original = nodes.get_linkedin_source
    nodes.get_linkedin_source = lambda: SpySource()
    try:
        set_target_countries([])
        with TestClient(app) as client:
            _run_and_wait(client)
        assert all("India" not in q for q in seen_queries)

        seen_queries.clear()
        set_target_countries(["India"])
        with TestClient(app) as client:
            _run_and_wait(client)
        assert any(q.endswith(" India") for q in seen_queries)
    finally:
        nodes.get_linkedin_source = original
        set_target_countries([])


def test_batch_generate_all_hits_daily_cap(batch_env, monkeypatch):
    from app.config import settings

    with TestClient(app) as client:
        monkeypatch.setattr(settings, "max_drafts_per_day", 1)
        run_id = _run_and_wait(client)
        assert client.get(f"/api/runs/{run_id}").json()["status"] == "completed"

        res = client.post("/api/queue/generate-all")
        body = res.json()
        assert body["generated"] == 1
        assert body["capped"] == 1  # the second pending lead left for tomorrow

        statuses = sorted(d["status"] for d in client.get("/api/queue").json())
        assert statuses == ["new", "pending"]
