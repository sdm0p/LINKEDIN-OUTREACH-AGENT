"""Run endpoints: trigger, poll, and the live search_posts validation pass.

The validation endpoint exists because the plan flags the exact
search_posts response schema as an open item — it runs one real search,
returns the raw response text and the parsed preview side by side, so the
normalizer can be confirmed against reality before downstream stages
trust it.
"""

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..search.base import SearchError, get_linkedin_source
from ..search.linkedin_mcp import _RECENTY_MAP
from ..services import keyword_service, run_service

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.post("")
async def trigger(payload: dict):
    recency = str(payload.get("recency", "24h"))
    if recency not in _RECENTY_MAP:
        return JSONResponse(status_code=400, content={"detail": "recency must be 24h, week, or month"})
    # keyword_limit: absent -> default LRU rotation size, "all" -> every
    # active keyword in one run, integer -> explicit cap.
    raw_limit = payload.get("keyword_limit", "default")
    keyword_limit: int | None
    if raw_limit == "all":
        keyword_limit = None
    elif raw_limit == "default":
        keyword_limit = keyword_service.KEYWORDS_PER_RUN
    else:
        try:
            keyword_limit = int(raw_limit)
        except (TypeError, ValueError):
            return JSONResponse(status_code=400, content={"detail": "keyword_limit must be an integer, 'all', or omitted"})
        if keyword_limit < 1:
            return JSONResponse(status_code=400, content={"detail": "keyword_limit must be >= 1"})
    run_id = await run_service.start_run(recency, keyword_limit=keyword_limit)
    return {"run_id": run_id}


@router.get("/latest")
async def latest() -> dict | None:
    return run_service.get_latest_run_payload()


@router.get("/{run_id}")
async def get_one(run_id: str):
    payload = run_service.get_run_payload(run_id)
    if payload is None:
        return JSONResponse(status_code=404, content={"detail": "Run not found."})
    return payload


@router.post("/validate-search")
async def validate_search(payload: dict):
    """Live validation pass: one real search_posts call, raw + parsed."""
    keyword = str(payload.get("keyword", "")).strip() or "hiring developer"
    recency = str(payload.get("recency", "24h"))
    if recency not in _RECENTY_MAP:
        return JSONResponse(status_code=400, content={"detail": "recency must be 24h, week, or month"})
    if get_linkedin_source().name != "linkedin":
        return JSONResponse(status_code=400, content={"detail": "validation call requires the mcp source"})
    source = get_linkedin_source()
    try:
        raw = await source.raw_tool(
            "search_posts", {"keywords": keyword, "date_posted": _RECENTY_MAP[recency]}
        )
        result = await source.search_posts(keyword, recency)
    except SearchError as exc:
        return JSONResponse(status_code=502, content={"detail": str(exc)})
    finally:
        await source.close()

    sample = result.posts[0].__dict__ if result.posts else None
    if sample is not None and "raw" in sample:
        sample = {k: v for k, v in sample.items() if k != "raw"}
    return {
        "keyword": keyword,
        "recency": recency,
        "raw": raw[:8000],
        "raw_length": len(raw),
        "parsed_count": result.raw_hit_count,
        "degraded": result.degraded,
        "sample_post": sample,
    }
