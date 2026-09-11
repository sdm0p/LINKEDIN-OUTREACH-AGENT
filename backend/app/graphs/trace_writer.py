"""Run trace plumbing: in-process registry + live persistence.

The registry lets graph nodes log to the same RunTrace instance the
orchestrator persists, without putting a live object into checkpointed
graph state (which must stay JSON-safe).

`persist_trace` is called at every batch boundary by the run orchestrator,
so the Run page shows progress while the pipeline is still executing —
not only after the run ends.
"""

import json
from datetime import UTC, datetime

from ..db import connect, get_db_path, get_run, save_run
from ..run_trace import RunTrace

_active_traces: dict[str, RunTrace] = {}


def register_trace(run_id: str, trace: RunTrace) -> None:
    _active_traces[run_id] = trace


def release_trace(run_id: str) -> None:
    _active_traces.pop(run_id, None)


def trace_for(run_id: str) -> RunTrace:
    return _active_traces[run_id]


def persist_trace(run_id: str, trace: RunTrace, status: str, error: str | None = None,
                  summary: dict | None = None) -> None:
    conn = connect(get_db_path())
    try:
        row = get_run(conn, run_id)
        if row is None:
            return
        save_run(
            conn,
            {
                "id": run_id,
                "started_at": row["started_at"],
                "finished_at": (
                    datetime.now(UTC).isoformat(timespec="seconds")
                    if status in ("completed", "failed")
                    else None
                ),
                "status": status,
                "recency": row["recency"],
                "trace_json": json.dumps(trace.as_list()),
                "summary_json": json.dumps(summary) if summary else row["summary_json"],
                "error": error,
            },
        )
    finally:
        conn.close()
