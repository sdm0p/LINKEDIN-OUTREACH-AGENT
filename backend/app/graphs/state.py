"""Shared state for the graphs.

TypedDicts threaded through the nodes. Keep values JSON-checkpointable
(AsyncSqliteSaver serializes the state) — no live objects, only plain
data. (The RunTrace is held in an in-process registry, see trace_writer.)
"""

from typing import Any, TypedDict


class RunState(TypedDict, total=False):
    run_id: str
    recency: str
    # How many keywords this run searches: an int subset, or None for the
    # whole active pool. JSON-safe: None survives checkpointing fine.
    keyword_limit: int | None
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
    # DLQ replay outcome from the run-start hook (replayed/recovered/
    # still_failing/parked) — surfaced in the run summary.
    dlq_summary: dict
    # Control.
    finished: bool


class DraftState(TypedDict, total=False):
    lead_id: int
    drafted: int | None
    counts: dict[str, Any] | None
