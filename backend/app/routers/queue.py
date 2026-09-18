"""Review queue endpoints. Status transitions are manual (the user marks
items reviewed/sent/skipped in the UI); purge is manual only.

Draft generation is explicit and on-demand: leads arrive with status
'pending' and no draft text; only these two endpoints ever invoke the
draft graph (whose interrupt gate is passed by the request itself).
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..llm import LLMError
from ..services import queue_service

router = APIRouter(prefix="/api/queue", tags=["queue"])


@router.get("")
async def list_all(
    status: str | None = None,
    country: str | None = None,
    order: str = "added",
):
    if status is not None and status not in (
        "pending", "new", "reviewed", "sent", "skipped"
    ):
        return JSONResponse(status_code=400, content={"detail": "Invalid status filter."})
    if order not in ("added", "posted"):
        return JSONResponse(status_code=400, content={"detail": "Invalid order."})
    # Leads ingested before the location feature have no country (and dedup
    # means a rerun never re-reads those posts) — backfill them from their
    # stored post text so the filter actually has something to match. Cheap
    # (regex over text, no LLM/network) and idempotent.
    await queue_service.backfill_missing_locations()
    # Empty string = "location unknown" bucket; a name filters exactly that
    # country. Anything else the DB simply matches on.
    return await queue_service.list_queue(status, country=country, order=order)


@router.get("/countries")
async def countries():
    """Countries actually present in the queue (for the filter dropdown)."""
    return {"countries": await queue_service.list_countries()}


@router.patch("/{draft_id}/status")
async def set_status(draft_id: int, payload: dict):
    status = str(payload.get("status", ""))
    if status not in ("pending", "new", "reviewed", "sent", "skipped"):
        return JSONResponse(status_code=400, content={"detail": "Invalid status."})
    await queue_service.set_status(draft_id, status)
    return {"ok": True}


@router.post("/{lead_id}/generate")
async def generate_one(lead_id: int):
    """Draft one pending lead — the explicit human ask. Runs the
    checkpointed draft graph through its interrupt gate."""
    try:
        counts = await queue_service.generate_lead_draft(lead_id)
        return {"ok": True, **counts}
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    except LLMError as exc:
        return JSONResponse(status_code=502, content={"detail": str(exc)})


@router.post("/generate-all")
async def generate_all():
    """Draft every pending lead, oldest first, stopping at the daily cap.
    Per-lead failures are counted, not fatal."""
    result = await queue_service.generate_all_pending()
    return result


@router.post("/{lead_id}/fetch-job")
async def fetch_job(lead_id: int):
    """Fetch the attached job's details for one lead (explicit; costs a
    browser navigation). Uses the captured job id when the post carried a
    job card, else searches LinkedIn jobs for the lead's keyword."""
    try:
        return await queue_service.fetch_job_details(lead_id)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})


@router.post("/purge")
async def purge(payload: dict):
    """Manual purge. With a status, clears only that bucket (plus the
    dedup entries so those posts can be reprocessed); without one, clears
    everything. Never called automatically."""
    status = payload.get("status")
    if status is not None and status not in ("new", "reviewed", "sent", "skipped"):
        return JSONResponse(status_code=400, content={"detail": "Invalid status."})
    removed = await queue_service.purge(status)
    return {"removed": removed}
