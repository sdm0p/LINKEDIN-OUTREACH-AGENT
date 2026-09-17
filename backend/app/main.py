"""FastAPI app entrypoint. Run with:
    uv run uvicorn app.main:app --reload --port 8000
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

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


# ---------- static frontend (packaged/local-app mode) ----------

# In packaged deployments (Docker image, frozen desktop app) the built SPA
# ships alongside the backend and is served here. Local dev keeps using the
# Vite dev server on :5173 (its proxy hits these /api routes directly).
_DIST = Path(__file__).resolve().parent.parent / "frontend-dist"

if _DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str) -> FileResponse:
        """SPA fallback: client-side routes (e.g. /queue) get index.html;
        anything that resolves to a real file under dist/ is served as-is."""
        candidate = (_DIST / full_path).resolve()
        if (
            full_path
            and candidate.is_file()
            and candidate.is_relative_to(_DIST.resolve())
        ):
            return FileResponse(candidate)
        return FileResponse(_DIST / "index.html")
