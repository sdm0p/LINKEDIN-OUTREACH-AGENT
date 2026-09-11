"""Keyword pool service: generation, CRUD, and rotation selection.

Rotation: pinned keywords are always included (they are explicit user
overrides); the remaining slots are filled by least-recently-used, then
least-used count. This keeps per-run search volume low (plan: 4-6 per
run) while covering the whole pool over roughly a week.
"""

import asyncio
import json

from ..db import (
    connect,
    count_processed_posts,
    delete_keyword,
    get_db_path,
    insert_keyword,
    list_keywords,
    mark_keywords_used,
    oldest_processed_post_age_days,
    replace_unpinned_keywords,
    update_keyword,
)
from ..llm import get_llm_provider
from ..pipeline import keyword_generation
from . import resume_service

_db_lock = resume_service._db_lock

# How many keywords go out per run (plan: rotate 4-6 per run).
KEYWORDS_PER_RUN = 5


def _conn():
    return connect(get_db_path())


def _row_to_dict(row) -> dict:
    return {
        "id": row["id"],
        "text": row["text"],
        "tier": row["tier"],
        "pinned": bool(row["pinned"]),
        "active": bool(row["active"]),
        "last_used_at": row["last_used_at"],
        "times_used": row["times_used"],
        "created_at": row["created_at"],
    }


async def get_keywords() -> list[dict]:
    async with _db_lock:
        resume_service._ensure_db()
        conn = _conn()
        try:
            return [_row_to_dict(r) for r in list_keywords(conn)]
        finally:
            conn.close()


async def generate_and_store_pool() -> list[dict]:
    """LLM-generate a fresh pool. Unpinned keywords are replaced; pinned
    ones survive. Requires a parsed resume (roles + skills) on file."""
    async with _db_lock:
        resume_service._ensure_db()
        state = await resume_service.get_resume_state_assuming_lock()
        if not state.has_resume or not state.parsed_at:
            raise ValueError("Parse a resume first — keywords are generated from it.")

        expanded = state.roles_expanded or state.roles
        provider = get_llm_provider()
        pool = await keyword_generation.generate_keywords(
            expanded, state.skills, provider
        )

        rows = [
            {"text": t, "tier": "skill"} for t in pool["skill"]
        ] + [
            {"text": t, "tier": "title"} for t in pool["title"]
        ]
        conn = _conn()
        try:
            replace_unpinned_keywords(conn, rows)
            return [_row_to_dict(r) for r in list_keywords(conn)]
        finally:
            conn.close()


async def add_keyword(text: str, tier: str) -> dict | None:
    """Add a manual keyword; returns None if it already exists."""
    text = text.strip()
    if not text:
        raise ValueError("Keyword text is required.")
    if tier not in ("skill", "title"):
        raise ValueError("Tier must be 'skill' or 'title'.")
    async with _db_lock:
        resume_service._ensure_db()
        conn = _conn()
        try:
            created = insert_keyword(
                conn, text=text, tier=tier, created_at=_now()
            )
            if not created:
                return None
            row = conn.execute(
                "SELECT * FROM keywords WHERE text = ?", (text,)
            ).fetchone()
            return _row_to_dict(row)
        finally:
            conn.close()


async def edit_keyword(kw_id: int, fields: dict) -> None:
    if "text" in fields and not str(fields["text"]).strip():
        raise ValueError("Keyword text cannot be empty.")
    async with _db_lock:
        resume_service._ensure_db()
        conn = _conn()
        try:
            update_keyword(conn, kw_id, fields)
        finally:
            conn.close()


async def remove_keyword(kw_id: int) -> None:
    async with _db_lock:
        resume_service._ensure_db()
        conn = _conn()
        try:
            delete_keyword(conn, kw_id)
        finally:
            conn.close()


def select_for_run(rows: list[dict], limit: int = KEYWORDS_PER_RUN) -> list[dict]:
    """Pick this run's subset. Pure function so it is unit-testable.

    Pinned + active first, then active sorted by (last_used_at nulls first,
    times_used asc) — least-recently-used coverage across runs.
    """
    active = [r for r in rows if r["active"]]
    pinned = [r for r in active if r["pinned"]]
    unpinned = [r for r in active if not r["pinned"]]

    def _key(r: dict):
        return (r["last_used_at"] or "", r["times_used"])

    unpinned.sort(key=_key)
    return (pinned + unpinned)[:limit]


async def pick_for_run(run_id: str) -> list[dict]:
    """Select the run's keywords and mark them used. Returns the selection
    with run state embedded (used_ids stored by the caller's trace)."""
    async with _db_lock:
        resume_service._ensure_db()
        conn = _conn()
        try:
            rows = [_row_to_dict(r) for r in list_keywords(conn)]
        finally:
            conn.close()

    if not rows:
        raise ValueError(
            "Keyword pool is empty — generate keywords on the Keywords & roles page."
        )
    return select_for_run(rows)


async def commit_usage(kw_ids: list[int]) -> None:
    async with _db_lock:
        resume_service._ensure_db()
        conn = _conn()
        try:
            mark_keywords_used(conn, kw_ids)
        finally:
            conn.close()


def _now() -> str:
    from ..db import utcnow

    return utcnow()


# Retention numbers for the Settings page.
async def retention_stats() -> dict:
    async with _db_lock:
        resume_service._ensure_db()
        conn = _conn()
        try:
            return {
                "dedup_entries": count_processed_posts(conn),
                "oldest_entry_age_days": oldest_processed_post_age_days(conn),
            }
        finally:
            conn.close()


def trace_json(entries: list[dict]) -> str:
    return json.dumps(entries)
