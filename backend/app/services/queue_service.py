"""Review-queue service: qualify posts into leads, draft on demand.

Two distinct phases, deliberately separated:

Qualify (inside the LangGraph run graph, per keyword batch):
  dedup -> YoE filter -> extraction/classification -> (email | DM fallback)
  -> insert as a PENDING lead. No draft is generated here, ever.

Draft (on explicit request from the Review queue, via the draft graph):
  daily-cap check -> LLM draft -> row flips pending -> new.

Nothing sends. The daily cap applies at draft time, so browsing and
qualifying leads never burns the cap — only real drafts do.
"""

from datetime import UTC, datetime

from ..config import settings
from ..db import (
    connect,
    count_drafts_generated_on,
    get_db_path,
    insert_draft,
    record_processed_posts,
    save_generated_draft,
    seen_post_ids,
)
from ..llm import LLMError, get_llm_provider
from ..pipeline import draft_generation, extraction, yoe_filter
from ..search.base import Post
from . import resume_service

_db_lock = resume_service._db_lock


def _conn():
    return connect(get_db_path())


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ---------- phase 1: qualify (called by the run graph, per batch) ----------


async def ingest_posts(posts: list[Post], keyword: str, run_id: str, trace) -> dict:
    """Qualify one keyword's posts into pending leads. Returns counts for
    the trace. No LLM drafting happens here."""
    provider = get_llm_provider()
    counts = {"dedup_skipped": 0, "yoe_dropped": 0, "noise": 0,
              "no_contact": 0, "leads": 0, "errors": 0}

    async with _db_lock:
        resume_service._ensure_db()
        state = await resume_service.get_resume_state_assuming_lock()
        my_yoe = state.my_yoe

        conn = _conn()
        try:
            post_ids = [p.post_id for p in posts]
            already = seen_post_ids(conn, post_ids)
        finally:
            conn.close()

        processed: list[dict] = []
        new_posts = [p for p in posts if p.post_id not in already]
        counts["dedup_skipped"] = len(posts) - len(new_posts)
        if counts["dedup_skipped"]:
            trace.log("dedup", f"{counts['dedup_skipped']} posts already processed — skipped")

        for post in new_posts:
            # --- YoE filter ---
            kept, required = yoe_filter.passes_yoe_filter(post, my_yoe)
            if not kept:
                counts["yoe_dropped"] += 1
                trace.log(
                    "yoe-filter",
                    f"Dropped '{post.author_name or 'unknown'}' — requires {required}y, "
                    f"you have {my_yoe}y",
                )
                processed.append(_processed_row(post, keyword, run_id))
                continue
            if required:
                trace.log("yoe-filter", f"Kept '{post.author_name or 'unknown'}' — requires {required}y <= {my_yoe}y")

            # --- extraction / classification ---
            try:
                classification = await extraction.classify_post(post, provider)
            except LLMError as exc:
                # One post's failure must not sink the batch. Not marked
                # processed, so a later run can classify it again.
                counts["errors"] += 1
                trace.log(
                    "extraction",
                    f"Classification failed for {post.author_name or 'unknown'} — "
                    f"left for a later run ({str(exc)[:120]})",
                )
                continue
            company = classification["company"]
            role = classification["role"]
            emails = classification["emails"]
            verdict = classification["verdict"]

            if verdict != "hiring":
                counts["noise"] += 1
                trace.log("extraction", f"Noise (not a hiring post) from {post.author_name or 'unknown'} — dropped")
                processed.append(_processed_row(post, keyword, run_id, company))
                continue

            # --- contact resolution: email, else DM fallback ---
            contact_method = "email" if emails else "dm"
            contact_value = emails[0] if emails else None
            if not emails:
                query = " ".join(x for x in (post.author_name, company) if x)
                people = await _search_people_safe(query, trace)
                if people:
                    contact_value = people[0].get("profile_url") or ""
                    trace.log("extraction", f"No email — DM target resolved: {contact_value}")
                else:
                    counts["no_contact"] += 1
                    trace.log("extraction", f"No email and no DM target found for {post.author_name or 'unknown'} — dropped")
                    processed.append(_processed_row(post, keyword, run_id, company))
                    continue

            # --- queue as a pending lead (draft generated on demand) ---
            conn = _conn()
            try:
                insert_draft(
                    conn,
                    {
                        "post_id": post.post_id,
                        "run_id": run_id,
                        "keyword": keyword,
                        "company": company or None,
                        "role": role or None,
                        "author_name": post.author_name or None,
                        "author_headline": post.author_headline or None,
                        "author_profile_url": post.author_profile_url or None,
                        "post_url": post.post_url or None,
                        "contact_method": contact_method,
                        "contact_value": contact_value,
                        "yoe_required": required,
                        "classification": verdict,
                        "post_text": post.text,
                        "draft_text": None,
                        "drafted_at": None,
                        "status": "pending",
                        "created_at": _now(),
                        "updated_at": _now(),
                    },
                )
            finally:
                conn.close()
            counts["leads"] += 1
            trace.log(
                "queue",
                f"Lead queued: {role or 'role'} @ {company or post.author_name or 'unknown'} "
                f"({contact_method}) — draft on demand",
            )
            processed.append(_processed_row(post, keyword, run_id, company))

        if processed:
            conn = _conn()
            try:
                record_processed_posts(
                    conn,
                    [
                        {
                            "post_id": r["post_id"],
                            "company_family": r["company_family"],
                            "post_url": r["post_url"],
                        }
                        for r in processed
                    ],
                    run_id,
                )
            finally:
                conn.close()

    return counts


def _processed_row(post: Post, keyword: str, run_id: str, company: str | None = None) -> dict:
    return {
        "post_id": post.post_id,
        "company_family": company or yoe_filter.company_family(post),
        "post_url": post.post_url or None,
        "keyword": keyword,
        "run_id": run_id,
    }


async def _search_people_safe(query: str, trace) -> list[dict]:
    from ..search.base import SearchError, get_linkedin_source

    if not query.strip():
        return []
    try:
        source = get_linkedin_source()
        return await source.search_people(query)
    except SearchError as exc:
        trace.log("extraction", f"People search failed: {exc}")
        return []


# ---------- phase 2: draft (on demand, via the draft graph) ----------


async def generate_draft_for_row(row, resume_state) -> dict:
    """Generate and store one pending lead's draft. Called by the draft
    graph's generate node after the approval interrupt — no other code
    path drafts. Raises ValueError when the daily cap is reached (checked
    before the LLM call so browsing never burns quota)."""
    drafted_today = 0
    async with _db_lock:
        resume_service._ensure_db()
        conn = _conn()
        try:
            drafted_today = count_drafts_generated_on(conn, _today())
        finally:
            conn.close()
    if drafted_today >= settings.max_drafts_per_day:
        raise ValueError(
            f"Daily draft cap reached ({settings.max_drafts_per_day}) — "
            "generate more drafts tomorrow."
        )

    provider = get_llm_provider()
    post = Post(
        post_id=row["post_id"],
        text=row["post_text"] or "",
        post_url=row["post_url"] or "",
        author_name=row["author_name"] or "",
        author_headline=row["author_headline"] or "",
        author_profile_url=row["author_profile_url"] or "",
    )
    draft = await draft_generation.generate_draft(
        post,
        contact_method=row["contact_method"],
        company=row["company"] or "",
        role=row["role"] or "",
        state=resume_state,
        provider=provider,
    )
    text = (
        f"Subject: {draft['subject']}\n\n{draft['body']}"
        if row["contact_method"] == "email" and draft["subject"]
        else draft["body"]
    )

    async with _db_lock:
        resume_service._ensure_db()
        conn = _conn()
        try:
            save_generated_draft(conn, row["id"], text)
        finally:
            conn.close()
    return {"draft_id": row["id"], "contact_method": row["contact_method"]}


async def generate_lead_draft(lead_id: int) -> dict:
    """The explicit human 'ask': run the checkpointed draft graph for one
    lead. The graph interrupts at its approval gate; this resumes it with
    Command(resume=True) — the click that got us here."""
    from langgraph.types import Command

    from ..graphs import build_draft_graph, saver_session

    config = {"configurable": {"thread_id": f"draft-{lead_id}"}}
    async with saver_session() as checkpointer:
        graph = await build_draft_graph(checkpointer)
        # First invoke runs to the approval gate and stops (interrupt).
        await graph.ainvoke({"lead_id": lead_id}, config=config)
        # The user's request IS the approval: resume through the gate.
        out = await graph.ainvoke(Command(resume=True), config=config)
    if not out.get("drafted"):
        raise ValueError("Draft generation was not approved.")
    return out.get("counts") or {}


async def generate_all_pending() -> dict:
    """Draft every pending lead (subject to the daily cap). Stops early
    with 'capped' when the cap is hit; per-lead LLM failures are counted
    and skipped, not fatal."""
    rows = await list_pending()
    generated = failed = 0
    capped = 0
    for row in rows:
        try:
            await generate_lead_draft(row["id"])
            generated += 1
        except ValueError as exc:
            if "cap" in str(exc).lower():
                capped = len(rows) - generated - failed
                break
            failed += 1
        except LLMError:
            failed += 1
    return {"generated": generated, "failed": failed, "capped": capped}


async def count_pending() -> int:
    rows = await list_pending()
    return len(rows)


async def list_pending() -> list[dict]:
    """Pending lead rows, oldest first."""
    from ..db import list_pending_drafts

    async with _db_lock:
        resume_service._ensure_db()
        conn = _conn()
        try:
            return [dict(r) for r in list_pending_drafts(conn)]
        finally:
            conn.close()


# ---------- queue API support ----------


def _row_to_dict(row) -> dict:
    return dict(row)


async def list_queue(status: str | None = None) -> list[dict]:
    from ..db import list_drafts

    async with _db_lock:
        resume_service._ensure_db()
        conn = _conn()
        try:
            return [_row_to_dict(r) for r in list_drafts(conn, status)]
        finally:
            conn.close()


async def set_status(draft_id: int, status: str) -> None:
    from ..db import update_draft_status

    async with _db_lock:
        resume_service._ensure_db()
        conn = _conn()
        try:
            update_draft_status(conn, draft_id, status)
        finally:
            conn.close()


async def purge(status: str | None) -> int:
    from ..db import purge_drafts, purge_processed_posts

    async with _db_lock:
        resume_service._ensure_db()
        conn = _conn()
        try:
            n_drafts = purge_drafts(conn, status)
            n_posts = purge_processed_posts(conn)
        finally:
            conn.close()
    return n_drafts + n_posts
