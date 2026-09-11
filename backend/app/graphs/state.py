"""Shared state for the graphs.

TypedDicts threaded through the nodes. Keep values JSON-checkpointable
(AsyncSqliteSaver serializes the state) — no live objects, only plain
data. (The RunTrace is held in an in-process registry, see trace_writer.)
"""

from typing import Any, TypedDict


class RunState(TypedDict, total=False):
    run_id: str
    recency: str
    # Resume/keyword stage outputs.
    resume_ok: bool
    yoe: int
    keywords: list[dict]  # [{id, text, tier}]
    # Keyword batch loop.
    keyword_index: int
    batch: list[dict]  # dicts (Post fields) for the current keyword's posts
    # Cumulative summary.
    totals: dict
    raw_hits: int
    per_keyword: dict
    # Control.
    finished: bool


class DraftState(TypedDict, total=False):
    lead_id: int
    drafted: int | None
    counts: dict[str, Any] | None
