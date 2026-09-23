"""LangGraph orchestration for the pipeline.

The run graph executes one node step per keyword batch; every node
completion is a checkpoint and a live trace flush, so the Run page shows
progress mid-run and a crashed run can be inspected batch by batch.

Drafts are NOT part of the run: the run graph only qualifies leads into
the pending queue. Draft generation is a separate, explicit, human-
requested action routed through the draft graph's interrupt gate (see
queue_service.generate_lead_draft).
"""

import asyncio
import uuid
from datetime import UTC, datetime

from ..db import connect, get_db_path, get_run, latest_run, save_run
from ..llm import LLMError
from ..run_trace import RunTrace
from ..search.base import SearchError
from ..services import queue_service
from ..graphs import build_run_graph, saver_session
from ..graphs.trace_writer import persist_trace, register_trace, release_trace

_run_lock = asyncio.Lock()  # one run at a time


def _conn():
    return connect(get_db_path())


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


def _summary_from_state(values: dict, pending_count: int) -> dict:
    totals = values.get("totals", {}) or {}
    return {
        "keywords_used": [k["text"] for k in values.get("keywords", []) or []],
        "raw_hits": values.get("raw_hits", 0),
        "per_keyword": values.get("per_keyword", {}),
        "dedup_skipped": totals.get("dedup_skipped", 0),
        "gate_dropped": totals.get("gate_dropped", 0),
        "dlq": values.get("dlq_summary") or {},
        "yoe_dropped": totals.get("yoe_dropped", 0),
        "location_dropped": totals.get("location_dropped", 0),
        "noise": totals.get("noise", 0),
        "no_contact": totals.get("no_contact", 0),
        "leads_queued": totals.get("leads", 0),
        "llm_errors": totals.get("errors", 0),
        "pending_leads": pending_count,
        "drafts_created": 0,  # drafts are on-demand now, never in-run
    }


async def execute_run(
    run_id: str,
    recency: str,
    keyword_limit: int | None = None,
    keyword_ids: list[int] | None = None,
) -> None:
    """Drive the run graph, flushing the trace at every batch boundary.

    Every failure mode lands in `persist_trace`: no exception may leave a
    run stuck at status 'running'."""
    async with _run_lock:
        trace = RunTrace()
        registered = False
        try:
            register_trace(run_id, trace)
            registered = True
            _seed_run(run_id, recency)
            config = {"configurable": {"thread_id": run_id}}

            # DLQ replay first: recover yesterday's failures with today's
            # parser before spending any LinkedIn calls (design §7).
            try:
                dlq_summary = await queue_service.replay_dlq(trace)
                if dlq_summary["replayed"]:
                    trace.log(
                        "dlq",
                        f"DLQ replay: {dlq_summary['recovered']} recovered, "
                        f"{dlq_summary['still_failing']} still failing"
                        + (f", {dlq_summary['parked']} parked" if dlq_summary["parked"] else ""),
                    )
            except Exception as exc:  # noqa: BLE001 — replay must not sink the run
                dlq_summary = {"replayed": 0, "recovered": 0, "still_failing": 0, "parked": 0}
                trace.log("dlq", f"DLQ replay failed (run continues): {str(exc)[:150]}")

            initial: dict = {
                "run_id": run_id,
                "recency": recency,
                "keyword_limit": keyword_limit,
                "keyword_ids": keyword_ids,
                "dlq_summary": dlq_summary,
            }

            status = "running"
            error: str | None = None
            summary: dict | None = None
            try:
                async with saver_session() as checkpointer:
                    graph = await build_run_graph(checkpointer)
                    config = {"configurable": {"thread_id": run_id}}
                    initial = {
                        "run_id": run_id,
                        "recency": recency,
                        "keyword_limit": keyword_limit,
                    }
                    async for _chunk in graph.astream(initial, config=config):
                        # One stream event per node execution (per batch).
                        persist_trace(run_id, trace, "running")

                    state = await graph.aget_state(config)
                    values = state.values or {}
                    from . import queue_service

                    summary = _summary_from_state(
                        values, await queue_service.count_pending()
                    )
                status = "completed"
            except (SearchError, LLMError, ValueError) as exc:
                error = str(exc)
                trace.log("run", f"Run failed: {exc}")
                status = "failed"
            except Exception as exc:  # unexpected — still record it
                error = repr(exc)
                trace.log("run", f"Unexpected error: {error}")
                status = "failed"
            finally:
                persist_trace(run_id, trace, status, error=error, summary=summary)
        except Exception as outer:
            # Even setup (seed/registration) failed — record that, too.
            try:
                trace.log("run", f"Run setup failed: {outer!r}")
                persist_trace(
                    run_id, trace, "failed", error=repr(outer)
                )
            except Exception:
                pass
        finally:
            if registered:
                release_trace(run_id)


async def start_run(
    recency: str,
    keyword_limit: int | None = None,
    keyword_ids: list[int] | None = None,
) -> str:
    """Create the run row and schedule execution. Returns the run id.

    keyword_limit: int caps the LRU selection (the default rotation),
    None runs every active keyword in one go (the "search all" option).
    keyword_ids: explicit Run-page picker selection — overrides the
    rotation entirely and does not stamp usage."""
    run_id = uuid.uuid4().hex
    _seed_run(run_id, recency)
    asyncio.get_running_loop().create_task(
        execute_run(
            run_id,
            recency,
            keyword_limit=keyword_limit,
            keyword_ids=keyword_ids,
        )
    )
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
            "trace": _json_loads_or(row["trace_json"], []),
            "summary": _json_loads_or(row["summary_json"], None),
            "error": row["error"],
        }
    finally:
        conn.close()


def _json_loads_or(text: str | None, default):
    import json

    try:
        return json.loads(text) if text else default
    except Exception:
        return default


def get_latest_run_payload() -> dict | None:
    conn = _conn()
    try:
        row = latest_run(conn)
    finally:
        conn.close()
    if row is None:
        return None
    return get_run_payload(row["id"])
