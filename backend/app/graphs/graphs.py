"""Graph definitions.

Two graphs, both compiled with a SQLite checkpoint saver so state is
checkpointed after every node execution:

- run_graph: one node execution per keyword batch. Each iteration runs
  search -> ingest (dedup/YoE/classify -> pending leads) and is
  checkpointed, so a crash leaves an inspectable per-batch trail and the
  trace stays live on the Run page. NO draft generation anywhere — leads
  are queued for explicit, on-demand drafting.

- draft_graph: invoked per lead from the Review queue. Its interrupt()
  gate is a hard stop: the first invocation pauses at the gate with
  nothing drafted; only the endpoint's Command(resume=True) — the human
  click — carries it through.

Checkpointer lifecycle: AsyncSqliteSaver owns an aiosqlite connection that
is bound to the event loop it was opened on, so a saver must never be
shared across loops. Callers open one per operation with
`saver_session()` (async context manager) and pass it to the builders.
"""

from contextlib import asynccontextmanager
from pathlib import Path

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph

from .state import DraftState, RunState

CHECKPOINTS_FILENAME = "checkpoints.db"


@asynccontextmanager
async def saver_session(db_path: str | Path | None = None):
    """One AsyncSqliteSaver (and its connection) for the duration of one
    graph operation, on the caller's event loop."""
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from ..config import settings

    path = Path(db_path) if db_path else settings.data_dir / CHECKPOINTS_FILENAME
    async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:
        await saver.setup()  # create checkpoint tables if missing
        yield saver


async def build_run_graph(checkpointer: BaseCheckpointSaver):
    builder = StateGraph(RunState)

    builder.add_node("load_resume", _nodes().load_resume)
    builder.add_node("pick_keywords", _nodes().pick_keywords)
    builder.add_node("search_batch", _nodes().search_batch)
    builder.add_node("ingest_batch", _nodes().ingest_batch)
    builder.add_node("finish_run", _nodes().finish_run)

    builder.add_edge(START, "load_resume")
    builder.add_edge("load_resume", "pick_keywords")
    builder.add_edge("pick_keywords", "search_batch")
    builder.add_edge("search_batch", "ingest_batch")
    builder.add_conditional_edges(
        "ingest_batch",
        _nodes().more_keywords,
        {"next_batch": "search_batch", "finish": "finish_run"},
    )
    builder.add_edge("finish_run", END)

    return builder.compile(checkpointer=checkpointer)


async def build_draft_graph(checkpointer: BaseCheckpointSaver):
    builder = StateGraph(DraftState)

    builder.add_node("generate_draft", _nodes().generate_draft_node)
    builder.add_edge(START, "generate_draft")
    builder.add_edge("generate_draft", END)

    return builder.compile(checkpointer=checkpointer)


def _nodes():
    """Import lazily so importing this package never pulls the services
    stack (keeps unit tests and tooling light)."""
    from . import nodes

    return nodes
