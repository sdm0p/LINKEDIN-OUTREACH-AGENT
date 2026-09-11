"""Pipeline orchestrator: runs stages sequentially in a background task,
emitting a stage-level trace. Every run is manual (triggered from the UI) —
there is deliberately no scheduler anywhere in this codebase."""

import asyncio
import json
import random
from datetime import UTC, datetime

from ..db import connect, get_db_path, get_run, latest_run, save_run
from ..llm import LLMError
from ..run_trace import RunTrace
from ..search.base import SearchError, get_linkedin_source
from . import keyword_service, queue_service, resume_service

_run_lock = asyncio.Lock()  # one run at a time

PACING_SECONDS_RANGE = (3.0, 6.0)


def _conn():
    return connect(get_db_path())


def _persist_trace(run_id: str, trace: RunTrace, status: str, error: str | None = None,
                   summary: dict | None = None) -> None:
    conn = _conn()
    try:
        row = get_run(conn, run_id)
        if row is None:
            return
        save_run(
            conn,
            {
                "id": run_id,
                "started_at": row["started_at"],
                "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "status": status,
                "recency": row["recency"],
                "trace_json": json.dumps(trace.as_list()),
                "summary_json": json.dumps(summary) if summary else row["summary_json"],
                "error": error,
            },
        )
    finally:
        conn.close()


def _seed_run(run_id: str, recency: str) -> None:
    conn = _conn()
    try:
        save_run(
            conn,
            {
                "id": run_id,
                "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "finished_at": None,
                "status": "running",
                "recency": recency,
                "trace_json": "[]",
                "summary_json": None,
                "error": None,
            },
        )
    finally:
        conn.close()


async def execute_run(run_id: str, recency: str) -> None:
    """Full pipeline for one manual run. Slice 2 executes stages 1-4; the
    later slices slot in after search (dedup/YoE/extraction/drafts)."""
    async with _run_lock:
        trace = RunTrace()
        _seed_run(run_id, recency)
        try:
            # Stage 1+2: resume parse / role expansion (hash-cached).
            state = await resume_service.get_resume_state()
            if not state.has_resume or not state.parsed_at:
                raise ValueError("No parsed resume on file — upload one on the Resume page.")
            trace.log(
                "resume-parse",
                f"Resume '{state.file_name}' — "
                + ("loaded from cache" if state.cached else "freshly parsed"),
            )
            trace.log("resume-parse", f"YoE: {state.my_yoe}")
            trace.log(
                "role-expansion",
                f"{len(state.roles_expanded or [])} expanded roles available",
            )

            # Stage 3: keyword rotation.
            selected = await keyword_service.pick_for_run(run_id)
            trace.log(
                "keyword-generation",
                f"Selected {len(selected)} of pool for this run: "
                + "; ".join(f"[{k['tier']}] {k['text']}" for k in selected),
            )

            source = get_linkedin_source()

            # Stage 4: search per keyword, paced; stages 5-8 (dedup, YoE
            # filter, extraction, draft) run inline per keyword batch.
            total_raw = 0
            per_keyword_counts: dict[str, int] = {}
            totals = {"dedup_skipped": 0, "yoe_dropped": 0, "noise": 0,
                      "no_contact": 0, "capped": 0, "drafts": 0, "errors": 0}
            for i, kw in enumerate(selected):
                trace.log("search", f"[{i + 1}/{len(selected)}] Searching: {kw['text']}")
                try:
                    result = await source.search_posts(kw["text"], recency)
                except SearchError as exc:
                    trace.log("search", f"Search failed for '{kw['text']}': {exc}")
                    raise
                total_raw += result.raw_hit_count
                per_keyword_counts[kw["text"]] = result.raw_hit_count
                label = (
                    f"{result.raw_hit_count} posts found"
                    + (" — response degraded, needs review" if result.degraded else "")
                )
                trace.log("search", f"[{i + 1}/{len(selected)}] '{kw['text']}': {label}")

                if result.posts:
                    counts = await queue_service.ingest_posts(
                        result.posts, kw["text"], run_id, trace
                    )
                    for key in totals:
                        totals[key] += counts.get(key, 0)

                if i < len(selected) - 1:
                    delay = random.uniform(*PACING_SECONDS_RANGE)
                    trace.log(
                        "search",
                        f"Pausing {delay:.1f}s before next search (human pacing)",
                    )
                    await asyncio.sleep(delay)

            await keyword_service.commit_usage([k["id"] for k in selected])

            summary = {
                "keywords_used": [k["text"] for k in selected],
                "raw_hits": total_raw,
                "per_keyword": per_keyword_counts,
                "dedup_skipped": totals["dedup_skipped"],
                "yoe_dropped": totals["yoe_dropped"],
                "noise": totals["noise"],
                "no_contact": totals["no_contact"],
                "capped": totals["capped"],
                "drafts_created": totals["drafts"],
                "llm_errors": totals["errors"],
            }
            trace.log("search", f"Run complete — {total_raw} raw hits across {len(selected)} keywords")
            _persist_trace(run_id, trace, "completed", summary=summary)

        except (SearchError, LLMError, ValueError) as exc:
            trace.log("run", f"Run failed: {exc}")
            _persist_trace(run_id, trace, "failed", error=str(exc))
        except Exception as exc:  # unexpected — still record it
            trace.log("run", f"Unexpected error: {exc!r}")
            _persist_trace(run_id, trace, "failed", error=repr(exc))
        finally:
            # Release the container so the browser session is not held open.
            try:
                get_linkedin_source().close()
            except Exception:
                pass


async def start_run(recency: str) -> str:
    """Create the run row and schedule execution. Returns the run id."""
    import uuid

    run_id = uuid.uuid4().hex
    _seed_run(run_id, recency)
    asyncio.get_running_loop().create_task(execute_run(run_id, recency))
    return run_id


def get_run_payload(run_id: str) -> dict | None:
    conn = _conn()
    try:
        row = get_run(conn, run_id)
        if row is None:
            return None
        return {
            "id": row["id"],
            "status": row["status"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "recency": row["recency"],
            "trace": json.loads(row["trace_json"] or "[]"),
            "summary": json.loads(row["summary_json"]) if row["summary_json"] else None,
            "error": row["error"],
        }
    finally:
        conn.close()


def get_latest_run_payload() -> dict | None:
    conn = _conn()
    try:
        row = latest_run(conn)
    finally:
        conn.close()
    if row is None:
        return None
    return get_run_payload(row["id"])
