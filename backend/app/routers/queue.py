"""Review queue endpoints. Status transitions are manual (the user marks
items reviewed/sent/skipped in the UI); purge is manual only."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..services import queue_service

router = APIRouter(prefix="/api/queue", tags=["queue"])


@router.get("")
async def list_all(status: str | None = None):
    if status is not None and status not in ("new", "reviewed", "sent", "skipped"):
        return JSONResponse(status_code=400, content={"detail": "Invalid status filter."})
    return await queue_service.list_queue(status)


@router.patch("/{draft_id}/status")
async def set_status(draft_id: int, payload: dict):
    status = str(payload.get("status", ""))
    if status not in ("new", "reviewed", "sent", "skipped"):
        return JSONResponse(status_code=400, content={"detail": "Invalid status."})
    await queue_service.set_status(draft_id, status)
    return {"ok": True}


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
