"""FastAPI app entrypoint. Run with:
    uv run uvicorn app.main:app --reload --port 8000
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routers import keywords, queue, resume, runs, settings

app = FastAPI(title="LinkedIn Outreach Agent", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(resume.router)
app.include_router(keywords.router)
app.include_router(runs.router)
app.include_router(queue.router)
app.include_router(settings.router)


@app.get("/api/health")
async def health() -> dict:
    return {"ok": True}
