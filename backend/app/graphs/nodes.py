"""Node implementations for the run and draft graphs.

Nodes are thin: they fetch what they need, do one stage's work, and return
a partial state update. Heavy lifting stays in the existing services and
pipeline modules — the graph is orchestration, not business logic.

Traces: RunTrace objects are held in an in-process registry keyed by
run_id, never in graph state — checkpoint state must stay JSON-safe, and
a checkpoint round-trip would deserialize into a *different* trace object,
silently splitting the live trace in two.

Pacing: the human-pacing pause between keyword searches lives at the top
of search_batch (skipped for the first batch), so it lands between
checkpointed node executions.
"""

import asyncio
import random

from langgraph.types import interrupt

from ..search.base import Post, SearchError, get_linkedin_source

from ..services import keyword_service, queue_service
from ..services import resume_service as resume_service_root
from .state import RunState
from .trace_writer import trace_for

PACING_SECONDS_RANGE = (3.0, 6.0)


async def load_resume(state: RunState) -> dict:
    trace = trace_for(state["run_id"])
    resume_state = await resume_service_root.get_resume_state()
    if not resume_state.has_resume or not resume_state.parsed_at:
        raise ValueError("No parsed resume on file — upload one on the Resume page.")
    trace.log(
        "resume-parse",
        f"Resume '{resume_state.file_name}' — "
        + ("loaded from cache" if resume_state.cached else "freshly parsed"),
    )
    trace.log("resume-parse", f"YoE: {resume_state.my_yoe}")
    trace.log(
        "role-expansion",
        f"{len(resume_state.roles_expanded or [])} expanded roles available",
    )
    return {"resume_ok": True, "yoe": resume_state.my_yoe}


async def pick_keywords(state: RunState) -> dict:
    trace = trace_for(state["run_id"])
    selected = await keyword_service.pick_for_run(
        state["run_id"],
        limit=state.get("keyword_limit", keyword_service.KEYWORDS_PER_RUN),
        keyword_ids=state.get("keyword_ids"),
    )
    trace.log(
        "keyword-generation",
        f"Selected {len(selected)} of pool for this run: "
        + "; ".join(f"[{k['tier']}] {k['text']}" for k in selected),
    )
    return {
        "keywords": selected,
        "keyword_index": 0,
        "totals": {
            "dedup_skipped": 0, "gate_dropped": 0, "yoe_dropped": 0,
            "location_dropped": 0, "noise": 0, "no_contact": 0,
            "leads": 0, "errors": 0,
        },
        "raw_hits": 0,
        "per_keyword": {},
    }


async def search_batch(state: RunState) -> dict:
    """One keyword = one batch = one checkpoint. Search failures fail the
    run (the MCP/browser session is the shared bottleneck); everything
    downstream of search fails soft (counted, left for a later run)."""
    trace = trace_for(state["run_id"])
    i = state["keyword_index"]
    keywords = state["keywords"]
    kw = keywords[i]

    if i > 0:
        delay = random.uniform(*PACING_SECONDS_RANGE)
        trace.log("search", f"Pausing {delay:.1f}s before next search (human pacing)")
        await asyncio.sleep(delay)

    trace.log("search", f"[{i + 1}/{len(keywords)}] Searching: {kw['text']}")
    source = get_linkedin_source()

    try:
        # The keyword goes out as written. Do NOT append the target country
        # here: LinkedIn's Posts search is literal text matching, so
        # "hiring developer India" never returns city-only posts ("Bangalore",
        # "Hyderabad") or bare-Remote posts that never contain the token
        # "India". Country targeting is the ingest-side location filter's
        # job (city aliases, pre-LLM drop, post-LLM net).
        result = await source.search_posts(kw["text"], state["recency"])
    except SearchError as exc:
        trace.log("search", f"Search failed for '{kw['text']}': {exc}")
        raise
    label = (
        f"{result.raw_hit_count} posts found"
        + (" — response degraded, needs review" if result.degraded else "")
    )
    trace.log("search", f"[{i + 1}/{len(keywords)}] '{kw['text']}': {label}")
    if result.trace_note:
        trace.log("search", f"[{i + 1}/{len(keywords)}] {result.trace_note}")

    posts = [
        {
            "post_id": p.post_id, "text": p.text, "post_url": p.post_url,
            "author_name": p.author_name, "author_headline": p.author_headline,
            "author_profile_url": p.author_profile_url, "posted_at": p.posted_at,
            "job_id": p.raw.get("job_id", ""),
            "job_url": p.raw.get("job_url", ""),
        }
        for p in result.posts
    ]
    return {
        "batch": posts,
        "raw_hits": state["raw_hits"] + result.raw_hit_count,
        "per_keyword": {**state["per_keyword"], kw["text"]: result.raw_hit_count},
    }


async def ingest_batch(state: RunState) -> dict:
    """Dedup -> YoE filter -> classification -> insert as PENDING leads.
    No draft generation happens here — drafts are requested explicitly
    from the Review queue."""
    trace = trace_for(state["run_id"])
    i = state["keyword_index"]
    kw = state["keywords"][i]
    totals = dict(state["totals"])

    if state["batch"]:
        # Batch dicts come back from checkpointable state; rebuild Posts.
        posts = [
            Post(
                post_id=d["post_id"],
                text=d["text"],
                post_url=d.get("post_url", ""),
                author_name=d.get("author_name", ""),
                author_headline=d.get("author_headline", ""),
                author_profile_url=d.get("author_profile_url", ""),
                posted_at=d.get("posted_at", ""),
                raw={
                    "job_id": d.get("job_id", ""),
                    "job_url": d.get("job_url", ""),
                },
            )
            for d in state["batch"]
        ]
        counts = await queue_service.ingest_posts(
            posts, kw["text"], state["run_id"], trace
        )
        for key in totals:
            totals[key] += counts.get(key, 0)

    return {"totals": totals, "keyword_index": i + 1}


def more_keywords(state: RunState) -> str:
    """Conditional edge: loop back for the next keyword batch, or finish."""
    return "next_batch" if state["keyword_index"] < len(state["keywords"]) else "finish"


async def finish_run(state: RunState) -> dict:
    trace = trace_for(state["run_id"])
    trace.log(
        "search",
        f"Run complete — {state['raw_hits']} raw hits across "
        f"{len(state['keywords'])} keywords",
    )
    return {"finished": True}


# ---------- draft graph nodes ----------


async def generate_draft_node(state: dict) -> dict:
    """Generate one lead's draft. The interrupt() gate is a hard stop: the
    first invocation pauses here with nothing drafted; only a resume
    Command (the explicit human request) carries it past the gate."""
    from ..db import connect, get_db_path, get_draft

    lead_id = state["lead_id"]
    approval = interrupt({"lead_id": lead_id, "action": "generate_draft"})
    if approval is not True:
        raise ValueError("Draft generation was not approved.")

    resume_state = await resume_service_root.get_resume_state()

    conn = connect(get_db_path())
    try:
        row = get_draft(conn, lead_id)
    finally:
        conn.close()
    if row is None:
        raise ValueError(f"Lead {lead_id} not found.")
    if row["status"] != "pending":
        raise ValueError(
            f"Lead {lead_id} already has a draft (status: {row['status']})."
        )

    counts = await queue_service.generate_draft_for_row(row, resume_state)
    return {"drafted": lead_id, "counts": counts}
