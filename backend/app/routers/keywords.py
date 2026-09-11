"""Keywords & roles page endpoints."""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..llm import LLMError
from ..services import keyword_service

router = APIRouter(prefix="/api/keywords", tags=["keywords"])


@router.get("")
async def list_all() -> list[dict]:
    return await keyword_service.get_keywords()


@router.post("/generate")
async def generate_pool():
    try:
        return await keyword_service.generate_and_store_pool()
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    except LLMError as exc:
        return JSONResponse(status_code=502, content={"detail": str(exc)})


@router.post("")
async def add(payload: dict):
    try:
        created = await keyword_service.add_keyword(
            str(payload.get("text", "")), str(payload.get("tier", "skill"))
        )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    if created is None:
        return JSONResponse(status_code=409, content={"detail": "Keyword already exists."})
    return created


@router.patch("/{kw_id}")
async def edit(kw_id: int, payload: dict) -> JSONResponse:
    await keyword_service.edit_keyword(kw_id, payload)
    return JSONResponse(status_code=200, content={"ok": True})


@router.delete("/{kw_id}")
async def remove(kw_id: int) -> JSONResponse:
    await keyword_service.remove_keyword(kw_id)
    return JSONResponse(status_code=200, content={"ok": True})
