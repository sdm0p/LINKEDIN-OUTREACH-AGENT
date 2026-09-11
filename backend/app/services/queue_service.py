"""Review-queue service: turns kept posts into drafts.

Pipeline stages 5-8 live here as one ingest flow the run orchestrator
calls per keyword batch:

  dedup -> YoE filter -> extraction/classification -> (email | DM fallback)
  -> daily-cap check -> draft generation -> queue insert

Nothing sends. The daily cap stops processing entirely when reached so
un-processed posts stay unprocessed for a later run rather than being
silently burned.
"""

from datetime import UTC, datetime

from ..config import settings
from ..db import (
    connect,
    count_drafts_created_on,
    get_db_path,
    insert_draft,
    record_processed_posts,
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


async def ingest_posts(posts: list[Post], keyword: str, run_id: str, trace) -> dict:
    """Run stages 5-8 over one keyword's posts. Returns counts for the trace."""
    provider = get_llm_provider()
    counts = {"dedup_skipped": 0, "yoe_dropped": 0, "noise": 0, "no_contact": 0,
              "capped": 0, "drafts": 0, "errors": 0}

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
                # One post's failure must not sink the run. Not marked
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

            # --- daily cap ---
            conn = _conn()
            try:
                drafted_today = count_drafts_created_on(conn, _today())
            finally:
                conn.close()
            if drafted_today >= settings.max_drafts_per_day:
                counts["capped"] += 1
                trace.log(
                    "draft",
                    f"Daily cap reached ({settings.max_drafts_per_day}) — "
                    f"remaining posts left for a later run",
                )
                break

            # --- draft generation ---
            try:
                draft = await draft_generation.generate_draft(
                    post,
                    contact_method=contact_method,
                    company=company,
                    role=role,
                    state=state,
                    provider=provider,
                )
            except LLMError as exc:
                # Left unprocessed so a later run can retry the draft.
                counts["errors"] += 1
                trace.log(
                    "draft",
                    f"Draft generation failed for {post.author_name or 'unknown'} — "
                    f"will retry on a later run ({str(exc)[:120]})",
                )
                continue
            now = datetime.now(UTC).isoformat(timespec="seconds")
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
                        "draft_text": (
                            f"Subject: {draft['subject']}\n\n{draft['body']}"
                            if contact_method == "email" and draft["subject"]
                            else draft["body"]
                        ),
                        "created_at": now,
                        "updated_at": now,
                    },
                )
            finally:
                conn.close()
            counts["drafts"] += 1
            trace.log(
                "draft",
                f"Draft #{drafted_today + 1} ({contact_method}) for "
                f"{role or 'role'} @ {company or post.author_name or 'unknown'}",
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
