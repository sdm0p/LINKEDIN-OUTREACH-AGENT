# LinkedIn Outreach Agent

Local-only hiring-lead pipeline: parse a resume, expand roles, and (in
upcoming slices) search LinkedIn hiring posts, filter, extract contacts, and
draft outreach for **manual review**. Nothing ever sends automatically, and
runs are only ever triggered by hand from the UI.

Plan and decisions: see `linkedin-outreach-agent-plan.md` (source of truth).

## Status — slice 1 (for review)

Built: project scaffolding, LLM provider interface + Gemini implementation,
resume parse (hash-cached, YoE via ceil), role expansion (cached alongside),
Resume page, Settings status stubs, placeholder pages.

Not built yet (next slices): keyword generation, LinkedIn MCP search, dedup,
YoE post filter, extraction/classification, draft generation, review queue,
run trace UI.

## Prerequisites

- Python 3.12+ with [uv](https://docs.astral.sh/uv/)
- Node 18+
- A free Gemini API key: https://aistudio.google.com/apikey

## Backend

```bash
cd backend
cp .env.example .env        # then set GEMINI_API_KEY
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

Tests: `uv run pytest`

## Frontend

```bash
cd frontend
npm install
npm run dev                 # http://localhost:5173, proxies /api to :8000
```

## Notes

- **LLM provider**: all calls go through `backend/app/llm/base.py`;
  swapping providers is a one-file change (add a module, update the factory
  in `backend/app/llm/__init__.py`).
- **Resume cache**: keyed by SHA-256 of the file bytes. Re-uploading an
  identical PDF skips the LLM entirely; Re-parse forces a fresh run using the
  stored file.
- **LinkedIn MCP**: invoked per run over stdio as
  `docker run -i --rm -v linkedin-mcp-session:/home/pwuser/.linkedin-mcp stickerdaniel/linkedin-mcp-server:latest`.
  The session lives in the named Docker volume `linkedin-mcp-session`
  (Windows bind mounts break the server's profile-creation ACL hardening).
  One-time login:
  `docker run -it --rm -v linkedin-mcp-session:/home/pwuser/.linkedin-mcp -p 127.0.0.1:6080:6080 stickerdaniel/linkedin-mcp-server:latest --login --login-viewer`
  — open the printed loopback URL and sign in. The app never handles the
  raw cookie.
- **LLM model**: defaults to `gemini-2.5-flash`; override with `GEMINI_MODEL`
  in `backend/.env`. Rate limits are per model — switching models is the
  quickest way onto a fresh free-tier quota.
- **No sending, no scheduling**: no SMTP client, no LinkedIn message-sending
  calls, no cron. That is a permanent constraint of this project, not a
  TODO.
