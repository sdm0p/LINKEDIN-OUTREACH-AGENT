"""Resume page endpoints: state, upload, reparse."""

import json

from fastapi import APIRouter, File, UploadFile
from fastapi.responses import JSONResponse

from ..llm import LLMError
from ..models import ResumeState
from ..run_trace import RunTrace
from ..services import resume_service

router = APIRouter(prefix="/api/resume", tags=["resume"])


@router.get("", response_model=ResumeState)
async def get_resume() -> ResumeState:
    return await resume_service.get_resume_state()


@router.post("/upload", response_model=ResumeState)
async def upload_resume(file: UploadFile = File(...)) -> ResumeState:
    data = await file.read()
    file_name = file.filename or "resume.pdf"

    error = resume_service.validate_pdf(data, file_name)
    if error:
        return JSONResponse(status_code=400, content={"detail": error})

    trace = RunTrace()
    try:
        state = await resume_service.upload_resume(data, file_name, trace)
    except LLMError as exc:
        return JSONResponse(status_code=502, content={"detail": str(exc)})
    return state


@router.post("/reparse", response_model=ResumeState)
async def reparse_resume() -> ResumeState:
    trace = RunTrace()
    try:
        state = await resume_service.reparse_resume(trace)
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
    except LLMError as exc:
        return JSONResponse(status_code=502, content={"detail": str(exc)})
    return state
